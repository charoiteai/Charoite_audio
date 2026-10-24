import Foundation

/// Сборка хода чата с памятью: запрос к поиску, параметры поиска, основа
/// системного промпта, блок графа и тело запроса к Ollama (№632).
///
/// До этого всё жило внутри `LocalChatService.stream`, а пробы и бенч памяти
/// собирали свой ход сами: 5×1200 вместо 8×800, свой промпт, без бюджета и
/// без правила соседей по [[ссылке]]. Бенч мерил не тот путь, которым отвечает
/// чат. Здесь — одно место для чата и проб; сеть и состояние экрана остаются
/// у `LocalChatService`.
enum ChatRetrieval {
    /// Параметры поиска для блока графа.
    struct Params: Equatable {
        let limit: Int
        let snippet: Int
        let budget: Int

        /// Как у чата: бюджет задаётся поиску, а не срезается по хвосту —
        /// prefix(5000) резал последний источник на полуслове. 25×1000 вместо
        /// 8×800/5000 — бенч №743 на 65 фактах: в ответе 37 против 28, в выдаче
        /// 55 против 43; синтез медиана 15 с против 4 с. Только вместе с окном
        /// хода (`fit`): прежний brain-путь (снят в №754) бюджета не знал и отдавал 76–88 тыс. знаков.
        static let chat = Params(limit: 25, snippet: 1000, budget: 25_000)
    }

    /// Окно модели в токенах — один бюджет на весь ход (№743): системный
    /// промпт, блок графа, история и вопрос вместе не длиннее
    /// `num_ctx − num_predict`. Раньше было три бюджета в знаках, сумму не
    /// видел никто: прежний brain-путь отдавал 18–26 тыс. знаков при «бюджете» 5000,
    /// и при переполнении Ollama молча срезает НАЧАЛО промпта — системный
    /// промпт с правилом честности.
    enum Window {
        static let context = 32_768
        static let answer = 2_048
        /// Разметка чата (роли, служебные токены) — 512; ещё 1 024 — на
        /// неточность оценки на обычном тексте. Случайный текст (base64,
        /// случайные буквы) оценка занижает на 20–40 % — запас его не покрывает;
        /// мерила в ходе пока нет, обратная связь по `prompt_eval_count` ответа —
        /// №637 (out2, DS I1; out3, Haiku I-3).
        static let reserve = 1_536
        static let perMessage = 8
        /// Потолок истории — прежние 12 000 знаков кириллицы: длинный
        /// разговор не вытесняет память целиком.
        static let history = 6_000
        static var input: Int { context - answer - reserve }
    }

    /// Оценка токенов сверху НА ТЕКСТЕ ГРАФА — по правилам претокенизатора, а не по
    /// длине текста. Цифра — всегда токен, пробел перед цифрой или знаком —
    /// свой токен, перевод строки — токен, у буквенного прогона — не меньше
    /// токена; внутри прогона латиница по ⅓, смешанный регистр внутри слова
    /// (base64, хэши) — по ½, кириллица ½, иероглиф BMP и прочий не-ASCII знак 1,
    /// 4 байта UTF-8 — 4.
    /// Замер токенизатором Qwen3.6-35B (20.10): по 11 097 заметкам графа
    /// оценка/факт не ниже 1,18 (медиана 1,59), кроме вырожденного повтора
    /// одной буквы; цифры через пробел 1,00, лог 1,04, hex 1,00, base64 0,82.
    /// Прежнее правило «по длине знака в UTF-8» давало цифрам через пробел
    /// 0,67 и base64 0,64 (out1, DS C1). Ниже факта остаётся случайный текст:
    /// base64 0,81, случайные латинские буквы 0,63, кириллица 0,61–0,68 —
    /// у BPE слияния зависят от частоты пары, правилом их не поймать.
    static func estimatedTokens(_ text: String) -> Int {
        var sixths = 0          // в шестых долях токена: целые числа, без дробей
        var run = 0, runSixths = 0, asciiLetters = 0, mixedCase = false, prevLower = false
        func closeRun() {
            // смешанный регистр дорожает только у ASCII-букв: ⅓ → ½ (out2, Haiku I2)
            if run > 0 { sixths += max(6, runSixths + (mixedCase ? asciiLetters : 0)) }
            run = 0; runSixths = 0; asciiLetters = 0; mixedCase = false; prevLower = false
        }
        let scalars = Array(text.unicodeScalars)
        for (i, scalar) in scalars.enumerated() {
            let bytes = scalar.utf8.count
            if scalar.properties.isAlphabetic {
                if bytes == 1 {
                    if scalar.properties.isUppercase && prevLower { mixedCase = true }
                    prevLower = scalar.properties.isLowercase
                    runSixths += 2; asciiLetters += 1
                } else {
                    prevLower = false
                    runSixths += bytes == 2 ? 3 : bytes == 3 ? 6 : 24
                }
                run += 1
                continue
            }
            // комбинирующий знак — часть буквы: прогон не рвёт и не стоит ничего (out3, DS M4)
            let category = scalar.properties.generalCategory
            if category == .nonspacingMark || category == .spacingMark || category == .enclosingMark { continue }
            closeRun()
            switch bytes {
            case 1:
                if scalar.properties.isWhitespace && scalar != "\n" {
                    // пробел липнет к следующему слову; перед цифрой, знаком
                    // и другим пробелом — отдельный токен
                    let next = i + 1 < scalars.count ? scalars[i + 1] : nil
                    sixths += next?.properties.isAlphabetic == true ? 2 : 6
                } else {
                    sixths += 6     // цифра, знак, перевод строки
                }
            case 2, 3: sixths += 6  // «», ×, °, неразрывный пробел; «—», «…», знаки CJK (out2, Sonnet M3)
            default: sixths += 24   // эмодзи
            }
        }
        closeRun()
        return (sixths + 5) / 6
    }

    static func messageTokens(_ text: String) -> Int { estimatedTokens(text) + Window.perMessage }

    /// Выдача поиска под окно хода: куски (`Outcome.parts`) выбрасываются
    /// целиком, начиная со слабейшего, пока блок графа не уместится рядом с
    /// системным промптом и уже выбранным ходом (`turn` — история и вопрос:
    /// окно истории считается раньше памяти, `historyWindow`). Чипы (`rels`) —
    /// по оставшимся, а модель узнаёт о срезе строкой в блоке. Не уместился ни
    /// один кусок — выдачи нет, а вердикт «поиск неполный»: «в памяти этого
    /// нет» было бы ложью о памяти.
    static func fit(_ outcome: ArchiveSearch.Outcome, system: String, turn: [ChatContextWindow.Entry],
                    lang: String = L.lang) -> ArchiveSearch.Outcome {
        let room = Window.input - messageTokens(system) - turn.reduce(0) { $0 + messageTokens($1.text) }
        func fits(_ o: ArchiveSearch.Outcome) -> Bool {
            estimatedTokens(contextBlock(o, lang: lang).block) <= room
        }
        guard !fits(outcome) else { return outcome }
        let marker = outcome.text.hasPrefix(ArchiveSearch.lowConfidenceMarker)
            ? ArchiveSearch.lowConfidenceMarker : ""
        var parts = outcome.parts ?? [.init(text: outcome.text, rel: nil, rank: 0)]
        let total = parts.count
        func trimmed(_ kept: [ArchiveSearch.Part]) -> ArchiveSearch.Outcome {
            var seen = Set<String>()
            let rels = outcome.rels.map { _ in kept.compactMap(\.rel).filter { seen.insert($0).inserted } }
            let cut = L.t("показаны \(kept.count) из \(total) кусков выдачи, остальное не поместилось в окно модели",
                          "\(kept.count) of \(total) result pieces shown, the rest did not fit the model window",
                          "仅显示 \(kept.count)/\(total) 段检索结果，其余超出模型窗口", lang: lang)
            return ArchiveSearch.Outcome(
                text: marker + kept.map(\.text).joined(separator: "\n\n"), rels: rels,
                verdict: outcome.verdict, note: outcome.note.isEmpty ? cut : outcome.note + "; " + cut,
                parts: kept)
        }
        // цена куска считается один раз: пересборка блока на каждом выбросе
        // гоняла оценку по всей выдаче на главном акторе (out1, Sonnet M2)
        var costs = parts.map { estimatedTokens($0.text) + 2 }
        var spent = costs.reduce(0, +)
        let frame = estimatedTokens(contextBlock(trimmed([]), lang: lang).block)
        while let weakest = parts.indices.max(by: { parts[$0].rank < parts[$1].rank }) {
            parts.remove(at: weakest)
            spent -= costs.remove(at: weakest)
            guard !parts.isEmpty else { break }
            // сумма цен — прикидка; решает оценка собранного блока
            if frame + spent <= room, fits(trimmed(parts)) { return trimmed(parts) }
        }
        let overflow = L.t("выдача не поместилась в окно модели", "the results did not fit the model window",
                           "检索结果超出模型窗口", lang: lang)
        return ArchiveSearch.Outcome(text: "", rels: outcome.rels.map { _ in [] }, verdict: .unverified,
                                     note: outcome.note.isEmpty ? overflow : outcome.note + "; " + overflow,
                                     parts: [])
    }

    /// Ход из одного вопроса — для проб и тестов.
    static func fit(_ outcome: ArchiveSearch.Outcome, system: String, prompt: String,
                    lang: String = L.lang) -> ArchiveSearch.Outcome {
        fit(outcome, system: system, turn: [.init(role: "user", text: prompt)], lang: lang)
    }

    /// История под окно — ДО памяти: не больше `Window.history` и не больше
    /// четверти того, что осталось после системного промпта и вопроса; целыми
    /// ходами, вопрос — последним. При «истории — остаток» полная выдача
    /// оставляла диалогу 0–1,5 тыс. токенов, и второй вопрос терял первый
    /// (out1: Sonnet I1, DS I3, Haiku I-1).
    static func historyWindow(_ history: [ChatContextWindow.Entry], prompt: String,
                              system: String) -> [ChatContextWindow.Entry] {
        let free = Window.input - messageTokens(system) - messageTokens(prompt)
        let room = min(Window.history, max(0, free / 4))
        return ChatContextWindow.select(history: history, prompt: prompt,
                                        budget: room + messageTokens(prompt), cost: messageTokens)
    }

    /// Блок графа для системного промпта и что из него нужно экрану.
    struct Context: Equatable {
        /// Решение поиска; по нему выбраны шапка и правило честности блока.
        let verdict: ArchiveSearch.Verdict
        /// Выдача поиска без маркера слабых совпадений.
        let text: String
        /// Готовый блок для системного промпта: шапка, правило, выдача.
        /// Есть при любом вердикте — правило отсутствия живёт только здесь.
        let block: String
        /// Фрагменты не прошли гейт (локально) или помечены сервером «⚠» —
        /// маркер слабых совпадений: чипов нет, как до №634 (out2, Sonnet I3).
        /// По маркеру, не по вердикту: маркер ставит сам поиск, `unverified`
        /// с выдачей приходит с ним; сильная лексика без семантики —
        /// `confident` с чипами, как до №634.
        let hidesSources: Bool
        /// Подвал «слабые совпадения» — только у проверенного «слабо»;
        /// неполный поиск подвал называет своими словами (out3, DS I6).
        var weak: Bool { verdict == .weak && !text.isEmpty }
        /// Подвал «поиск неполный»: выдача есть, охват или семантика не
        /// доказаны — и при видимых чипах, как велит промпт (out4, Sonnet M2).
        var searchIncomplete: Bool { verdict == .unverified && !text.isEmpty }
        /// Строка статуса — из той же таблицы, что и правило честности.
        var status: String { ChatRetrieval.honesty(verdict, hasText: !text.isEmpty).status }
    }

    /// Запрос к поиску из разговора. Поиск по одной последней реплике
    /// («а что дальше?») находил мусор: тему несут ПОСЛЕДНИЕ вопросы вместе,
    /// свежий — главный.
    static func query(from messages: [LocalChatService.Message]) -> String {
        String((messages.filter { $0.role == "user" }.suffix(3).map(\.text)
            .joined(separator: " ") as String).suffix(500))
    }

    /// Основа системного промпта до блока графа. Промпт локализован целиком:
    /// при sufler.language: en интерфейс и поиск отвечали по-английски, а
    /// чат — по-русски, потому что этого требовала зашитая строка.
    static func baseSystem(inferenceIsLocal: Bool) -> String {
        var system = L.t("""
        Ты — Чароит, ассистент владельца этого Mac. \
        Отвечай по-русски, кратко и по делу. ЧЕСТНОСТЬ: отвечай только \
        по данным из блоков ниже и из разговора, не сочиняй; как быть, если по \
        теме ничего нет, сказано в блоке графа. Ты помнишь весь текущий \
        диалог — «а что я спрашивал», «продолжи», «а по второму пункту» относятся \
        к нему. Сопоставляй факты из разных встреч: называй повторяющиеся темы, \
        расхождения и что изменилось со временем, с датами и источниками из блока \
        графа. На вопрос «кто ты» — «Чароит, ассистент», без имени \
        вендора модели.
        """, """
        You are Charoite, the assistant of this Mac's owner. \
        Answer in English, briefly and to the point. HONESTY: answer only from the \
        blocks below and from this conversation, do not invent; what to do when \
        nothing is found is stated in the graph block. You remember \
        the whole current dialogue — "what did I ask", "go on", "what about the \
        second point" refer to it. Cross-reference facts across meetings: name \
        recurring topics, contradictions and what changed over time, with dates and \
        sources from the graph block. To "who are you" — "Charoite, an \
        assistant", without naming the model vendor.
        """, """
        你是 Charoite，这台 Mac 主人的助手。用中文简洁地回答。\
        诚实原则：只依据下面的资料块和当前对话作答，不要编造；若未找到相关内容，\
        如何处理见图谱资料块中的说明。你记得整段对话——「我刚才问了什么」\
        「继续」「第二点呢」都指向它。跨会议比对事实：指出反复出现的主题、\
        分歧以及随时间发生的变化，并附上图谱资料块中的日期与来源。\
        被问「你是谁」时回答「Charoite，助手」，不要提模型厂商。
        """)
        system += inferenceIsLocal
            ? L.t("\nМодель отвечает на этом Mac.", "\nThe model responds on this Mac.", "\n模型在此 Mac 上生成回复。")
            : L.t("\nМодель отвечает на другом сервере, явно выбранном в настройках; не называй этот ответ офлайн или локальным.",
                  "\nThe model responds on another server explicitly chosen in settings; do not call this response offline or local.",
                  "\n模型在设置中明确选择的其他服务器上生成回复；请勿称此回复为离线或本地。")
        return system
    }

    /// Блок графа из выдачи поиска. Решение «нашлось / слабо / пусто»
    /// принимает поиск (`Outcome.verdict`), а промпт его не перерешивает:
    /// правило «в памяти этого нет» звучало при любой выдаче, и модель
    /// говорила его при найденном факте — 17 из 55 ответов бенча №743 (№634).
    /// Маркер слабых совпадений (`ArchiveSearch.lowConfidenceMarker`)
    /// снимается здесь и только здесь.
    static func contextBlock(_ outcome: ArchiveSearch.Outcome, lang: String = L.lang) -> Context {
        var text = outcome.text
        let marked = text.hasPrefix(ArchiveSearch.lowConfidenceMarker)
        if marked { text.removeFirst(ArchiveSearch.lowConfidenceMarker.count) }
        // пустой текст с «найдено» не бывает: без выдачи опоры нет
        let verdict: ArchiveSearch.Verdict = text.isEmpty && outcome.verdict == .confident
            ? .empty : outcome.verdict
        let h = honesty(verdict, hasText: !text.isEmpty, lang: lang)
        var block = "\n\n[\(h.head)]\n\(h.rule)"
        // оговорка сервера (почему слабо, где не искали) — при любой выдаче:
        // ради неё разбор и держит `note` (out1, DS M1)
        if !outcome.note.isEmpty { block += "\n(\(outcome.note))" }
        if !text.isEmpty { block += "\n" + text }
        return Context(verdict: verdict, text: text, block: block, hidesSources: marked)
    }

    /// Блок хода с выключенной памятью: графа не искали, и модель не вправе
    /// ни ссылаться на память, ни говорить, что там чего-то нет.
    static func memoryOffBlock(lang: String = L.lang) -> String {
        "\n\n[" + L.t("ПАМЯТЬ ВЫКЛЮЧЕНА в этом ходе — граф встреч не искали",
                       "MEMORY IS OFF for this turn — the meeting graph was not searched",
                       "本轮记忆已关闭——未检索会议图谱", lang: lang) + "]\n"
            + L.t("О фактах встреч и людях ничего не утверждай — скажи, что память выключена.",
                  "Do not state facts about meetings or people — say that memory is off.",
                  "不要陈述会议或人物的事实——说明记忆已关闭。", lang: lang)
    }

    /// Шапка и правило честности по вердикту — одна таблица на три языка.
    /// `allowsAbsence` — правило
    /// велит формулу отсутствия: её право есть только у проверенного «пусто»
    /// и «слабо»; тест и бенч читают это поле, а не подстроку перевода.
    struct Honesty: Equatable {
        let head: String
        let rule: String
        let allowsAbsence: Bool
        /// Строка статуса чата: «пусто» не пишется там, где поиск был неполным
        /// или графа нет (out3, DS I9: была отдельным switch).
        let status: String
    }

    /// Формула отсутствия, которой велят правила `weak` и `empty`.
    static func absenceFormula(lang: String = L.lang) -> String {
        L.t("«в памяти этого нет»", "\"that is not in memory\"", "「记忆中没有这条」", lang: lang)
    }

    static func honesty(_ verdict: ArchiveSearch.Verdict, hasText: Bool,
                        lang: String = L.lang) -> Honesty {
        let f = absenceFormula(lang: lang)
        // «слабые совпадения» — слова проверенного поиска; здесь поиск
        // неполный при любой выдаче (out1, DS I)
        let incomplete = L.t("граф: поиск неполный", "graph: search incomplete", "图谱：检索不完整", lang: lang)
        switch verdict {
        case .confident:
            return Honesty(head: L.t("ГРАФ ВСТРЕЧ — люди, системы, решения, документация",
                                     "MEETING GRAPH — people, systems, decisions, documentation",
                                     "会议图谱——人物、系统、决策、文档", lang: lang),
                           rule: L.t("Поиск нашёл совпадения: отвечай по фрагментам, начни с найденного — факт, дата, источник. Если точного ответа в них нет, назови, что нашлось и чего не хватает; отказом без перечня найденного не отвечай.",
                                     "The search found matches: answer from the fragments, starting with what was found — fact, date, source. If they hold no exact answer, name what was found and what is missing; do not refuse without listing what was found.",
                                     "检索找到了匹配：依据片段作答，先说找到的内容——事实、日期、来源。若片段中没有确切答案，说明找到了什么、缺少什么；不要在未列出所找到内容的情况下拒答。", lang: lang),
                           allowsAbsence: false,
                           status: L.t("🧠 граф подмешан", "🧠 graph mixed in", "🧠 已加入图谱", lang: lang))
        case .weak:
            // Не «отказ первым словом»: гейт 0.47/0.66 под отказ не калибровали,
            // а вопрос другими словами попадает сюда с ответом в выдаче (out1, Sonnet I2).
            return Honesty(head: L.t("ГРАФ ВСТРЕЧ — совпадения СЛАБЫЕ, вероятно не про вопрос; не выдавай за факт",
                                     "MEETING GRAPH — WEAK matches, probably not about the question; do not present them as fact",
                                     "会议图谱——匹配很弱，可能与问题无关；不要当作事实", lang: lang),
                           rule: L.t("Поиск проверил граф, точных совпадений нет. Если ближайшее найденное прямо отвечает на вопрос — ответь по нему и оговорись, что совпадение слабое; иначе скажи \(f) и что нашлось рядом.",
                                     "The search checked the graph and found no close match. If the nearest fragment directly answers the question, answer from it and say the match is weak; otherwise say \(f) and what was found nearby.",
                                     "检索已查过图谱，没有接近的匹配。若最接近的片段直接回答了问题，依据它作答并说明匹配较弱；否则说\(f)，并列出相近的内容。", lang: lang),
                           allowsAbsence: true,
                           status: L.t("граф: слабые совпадения", "graph: weak matches", "图谱：弱匹配", lang: lang))
        case .unverified:
            return hasText
                ? Honesty(head: L.t("ГРАФ ВСТРЕЧ — найдено только по словам, семантикой не проверено",
                                    "MEETING GRAPH — found by words only, not checked semantically",
                                    "会议图谱——仅按字词找到，未经语义检验", lang: lang),
                          rule: L.t("Отвечай по фрагментам, если они про вопрос. Если нет — скажи, что точного ответа не нашлось, а поиск был неполным; не утверждай, что в памяти этого нет.",
                                    "Answer from the fragments if they are about the question. If not, say no exact answer was found and the search was incomplete; do not claim it is not in memory.",
                                    "若片段与问题相关，依据片段作答。若无关，说明没有找到确切答案且检索不完整；不要断言记忆中没有这条。", lang: lang),
                          allowsAbsence: false, status: incomplete)
                : Honesty(head: L.t("ГРАФ ВСТРЕЧ — ничего не нашлось, поиск неполный",
                                    "MEETING GRAPH — nothing found, the search was incomplete",
                                    "会议图谱——未找到，检索不完整", lang: lang),
                          rule: L.t("Не утверждай, что в памяти этого нет: поиск был неполным. Скажи, что найти не удалось, и предложи переформулировать.",
                                    "Do not claim it is not in memory: the search was incomplete. Say it could not be found and suggest rephrasing.",
                                    "不要断言记忆中没有这条：检索不完整。说明未能找到，并建议换个说法。", lang: lang),
                          allowsAbsence: false, status: incomplete)
        case .empty:
            // «искали без: …» сервер пишет и при доказанном пусто (архив вне
            // индекса по схеме) — python считает это отсутствием
            // (`brain.absence_only`), модель называет пропуск (out1, Sonnet I5).
            return Honesty(head: L.t("ГРАФ ВСТРЕЧ — по запросу ничего не найдено",
                                     "MEETING GRAPH — nothing found for the query",
                                     "会议图谱——未找到与查询相关的内容", lang: lang),
                           rule: L.t("О фактах встреч и людях скажи \(f), не сочиняй; если в оговорке поиска сказано, где не искали, — назови это. Вопросы о самом разговоре и о тебе — по разговору.",
                                     "For facts about meetings and people, say \(f) and do not invent; if the search note says where it did not look, mention that. Questions about this conversation or about you — answer from the conversation.",
                                     "关于会议和人物的事实，说\(f)，不要编造；若检索说明中提到未搜索的范围，要指出。关于本次对话或关于你的问题——依据对话回答。", lang: lang),
                           allowsAbsence: true,
                           status: L.t("в графе пусто по теме", "graph has nothing on the topic", "图谱中无相关内容", lang: lang))
        case .unavailable:
            return Honesty(head: L.t("ГРАФ ВСТРЕЧ НЕДОСТУПЕН — папка графа не найдена",
                                     "MEETING GRAPH UNAVAILABLE — the graph folder was not found",
                                     "会议图谱不可用——未找到图谱文件夹", lang: lang),
                           rule: L.t("О фактах встреч скажи, что память сейчас недоступна; не говори, что этого там нет.",
                                     "For facts about meetings, say memory is unavailable right now; do not say it is not there.",
                                     "关于会议的事实，说明记忆当前不可用；不要说里面没有。", lang: lang),
                           allowsAbsence: false,
                           status: L.t("граф недоступен", "graph unavailable", "图谱不可用", lang: lang))
        }
    }

    /// Ответ модели — отказ: формула отсутствия в первой содержательной
    /// строке (заголовок «## Ответ» и метка «Ответ:» пропускаются, разметка
    /// снята). `lines: 2` — для вопроса без ответа в графе: правило велит
    /// сперва назвать найденное, затем — чего нет. Детектор бенча — здесь,
    /// рядом с формулой правил (out1, DS C1, Sonnet I3): список вступлений
    /// вроде «в предоставленных блоках» считал отказом обычный ответ.
    static let absencePhrases = [
        "в памяти этого нет", "в памяти нет", "этого нет в памяти", "нет в памяти",
        "в графе этого нет", "в графе нет", "в предоставленных данных нет", "в предоставленных блоках нет",
        "в блоках нет", "информации об этом нет", "точного ответа нет", "точного ответа не нашлось",
        "найти не удалось", "не удалось найти", "ничего не нашлось", "ничего не найдено",
        "not in memory", "not in the memory", "no information about", "no information on",
        "don't have information", "do not have information", "no exact answer", "no record of",
        "could not be found", "couldn't find",
        "nothing was found", "记忆中没有", "未能找到", "没有找到", "没有相关", "没有确切答案",
    ]

    static func isAbsence(_ answer: String, lines: Int = 1) -> Bool {
        let marks = CharacterSet(charactersIn: "#*>_-`").union(.whitespaces)
        func says(_ t: String) -> Bool { absencePhrases.contains { t.contains($0) } }
        let content = answer.split(whereSeparator: \.isNewline)
            .compactMap { raw -> (text: String, says: Bool)? in
                let t = String(raw.trimmingCharacters(in: marks).prefix(200)).lowercased()
                    .replacingOccurrences(of: "ё", with: "е").replacingOccurrences(of: "’", with: "'")
                guard !t.isEmpty else { return nil }
                // заголовок и короткая метка «Ответ:» — не содержание, если
                // сами не отказ: «Нет в памяти:» — отказ (out2, Sonnet I5)
                let label = raw.trimmingCharacters(in: .whitespaces).hasPrefix("#")
                    || (t.hasSuffix(":") && t.count <= 30)
                return label && !says(t) ? nil : (t, says(t))
            }
            .prefix(lines)
        return content.contains { $0.says }
    }

    /// Тело запроса `/api/chat`. `stream: false` и температура 0 — для проб:
    /// бенч сравнивает правки, а не разброс генерации.
    static func requestBody(model: String, messages: [[String: String]],
                            stream: Bool, temperature: Double = 0.35) -> [String: Any] {
        [
            "model": model,
            "messages": messages,
            "stream": stream,
            "think": false,           // критично: дефолтный thinking = ~10с молчания
            "keep_alive": "30m",
            // num_ctx задан явно: без него Modelfile-дефолт 262144 →
            // перезагрузка 23GB модели при переключении чатов и пустой 2-й ответ.
            // 32768 — потолок для 32 ГБ машины (KV-кэш ~3-5 ГБ при 23 ГБ модели);
            // ход под него собирают `fit` и `historyWindow` (№743). 64К на
            // 32 ГБ уходит в своп — не поднимать без 64 ГБ RAM.
            "options": ["temperature": temperature, "num_ctx": Window.context, "num_predict": Window.answer],
        ]
    }
}
