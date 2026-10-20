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
        /// prefix(5000) резал последний источник на полуслове.
        static let chat = Params(limit: 8, snippet: 800, budget: 5000)
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
        /// Опоры для ответа нет или она не доказана: чипов нет.
        var weak: Bool { verdict == .weak || verdict == .unverified }
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
        if text.hasPrefix(ArchiveSearch.lowConfidenceMarker) {
            text.removeFirst(ArchiveSearch.lowConfidenceMarker.count)
        }
        // пустой текст с «найдено» не бывает: без выдачи опоры нет
        let verdict: ArchiveSearch.Verdict = text.isEmpty && outcome.verdict == .confident
            ? .empty : outcome.verdict
        let (head, rule) = honesty(verdict, hasText: !text.isEmpty, lang: lang)
        var block = "\n\n[\(head)]\n\(rule)"
        if !outcome.note.isEmpty && text.isEmpty { block += "\n(\(outcome.note))" }
        if !text.isEmpty { block += "\n" + text }
        return Context(verdict: verdict, text: text, block: block)
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
    /// Формула отсутствия есть только у вердиктов, где поиск её доказал.
    static func honesty(_ verdict: ArchiveSearch.Verdict, hasText: Bool,
                        lang: String = L.lang) -> (head: String, rule: String) {
        switch verdict {
        case .confident:
            return (L.t("ГРАФ ВСТРЕЧ — люди, системы, решения, документация",
                        "MEETING GRAPH — people, systems, decisions, documentation",
                        "会议图谱——人物、系统、决策、文档", lang: lang),
                    L.t("Поиск нашёл совпадения: отвечай по фрагментам, начни с найденного — факт, дата, источник. Если точного ответа в них нет, назови, что нашлось и чего не хватает; «в памяти этого нет» без перечня найденного не говори.",
                        "The search found matches: answer from the fragments, starting with what was found — fact, date, source. If they hold no exact answer, name what was found and what is missing; do not say \"that is not in memory\" without listing what was found.",
                        "检索找到了匹配：依据片段作答，先说找到的内容——事实、日期、来源。若片段中没有确切答案，说明找到了什么、缺少什么；不要在未列出所找到内容的情况下说「记忆中没有这条」。", lang: lang))
        case .weak:
            return (L.t("ГРАФ ВСТРЕЧ — совпадения СЛАБЫЕ, вероятно не про вопрос; не выдавай за факт",
                        "MEETING GRAPH — WEAK matches, probably not about the question; do not present them as fact",
                        "会议图谱——匹配很弱，可能与问题无关；不要当作事实", lang: lang),
                    L.t("Поиск проверил граф и почти ничего не нашёл: скажи «в памяти этого нет», затем — что нашлось рядом, с оговоркой.",
                        "The search checked the graph and found almost nothing: say \"that is not in memory\", then what was found nearby, with a caveat.",
                        "检索已查过图谱，几乎没有找到：先说「记忆中没有这条」，再附带说明列出相近的内容。", lang: lang))
        case .unverified:
            return hasText
                ? (L.t("ГРАФ ВСТРЕЧ — найдено только по словам, семантикой не проверено",
                       "MEETING GRAPH — found by words only, not checked semantically",
                       "会议图谱——仅按字词找到，未经语义检验", lang: lang),
                   L.t("Отвечай по фрагментам, если они про вопрос. Если нет — скажи, что точного ответа не нашлось, а поиск был неполным; не утверждай, что в памяти этого нет.",
                       "Answer from the fragments if they are about the question. If not, say no exact answer was found and the search was incomplete; do not claim it is not in memory.",
                       "若片段与问题相关，依据片段作答。若无关，说明没有找到确切答案且检索不完整；不要断言记忆中没有这条。", lang: lang))
                : (L.t("ГРАФ ВСТРЕЧ — по словам ничего не нашлось, семантикой не проверено",
                       "MEETING GRAPH — nothing found by words, not checked semantically",
                       "会议图谱——按字词未找到，未经语义检验", lang: lang),
                   L.t("Не утверждай, что в памяти этого нет: поиск был неполным. Скажи, что найти не удалось, и предложи переформулировать.",
                       "Do not claim it is not in memory: the search was incomplete. Say it could not be found and suggest rephrasing.",
                       "不要断言记忆中没有这条：检索不完整。说明未能找到，并建议换个说法。", lang: lang))
        case .empty:
            return (L.t("ГРАФ ВСТРЕЧ — по запросу ничего не найдено",
                        "MEETING GRAPH — nothing found for the query",
                        "会议图谱——未找到与查询相关的内容", lang: lang),
                    L.t("О фактах встреч и людях скажи «в памяти этого нет», не сочиняй. Вопросы о самом разговоре и о тебе — по разговору.",
                        "For facts about meetings and people, say \"that is not in memory\" and do not invent. Questions about this conversation or about you — answer from the conversation.",
                        "关于会议和人物的事实，说「记忆中没有这条」，不要编造。关于本次对话或关于你的问题——依据对话回答。", lang: lang))
        case .unavailable:
            return (L.t("ГРАФ ВСТРЕЧ НЕДОСТУПЕН — папка графа не найдена",
                        "MEETING GRAPH UNAVAILABLE — the graph folder was not found",
                        "会议图谱不可用——未找到图谱文件夹", lang: lang),
                    L.t("О фактах встреч скажи, что память сейчас недоступна, а не что «в памяти этого нет».",
                        "For facts about meetings, say memory is unavailable right now instead of \"that is not in memory\".",
                        "关于会议的事实，说明记忆当前不可用，而不是说「记忆中没有这条」。", lang: lang))
        }
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
            // 32768 — потолок для 32 ГБ машины (KV-кэш ~3-5 ГБ при 23 ГБ модели):
            // система+граф (5К) + история (12К) + ответ дышат свободно. 64К на
            // 32 ГБ уходит в своп — не поднимать без 64 ГБ RAM.
            "options": ["temperature": temperature, "num_ctx": 32768, "num_predict": 2048],
        ]
    }
}
