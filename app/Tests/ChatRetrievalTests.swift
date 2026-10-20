import XCTest
@testable import CharoiteApp

/// Сборка хода чата с памятью (№632): ожидания — литералами, снятыми с
/// прежнего тела `LocalChatService.stream`, тело запроса — по полям
/// разобранного JSON.
final class ChatRetrievalTests: XCTestCase {
    private func msg(_ role: String, _ text: String) -> LocalChatService.Message {
        LocalChatService.Message(role: role, text: text)
    }

    func testПараметрыПоискаКакУЧата() {
        XCTAssertEqual(ChatRetrieval.Params.chat, ChatRetrieval.Params(limit: 8, snippet: 800, budget: 5000))
    }

    func testЗапросИзТрёхПоследнихВопросов() {
        let history = [msg("user", "раз"), msg("assistant", "ответ"), msg("user", "два"),
                       msg("user", "три"), msg("assistant", "ещё"), msg("user", "четыре")]
        XCTAssertEqual(ChatRetrieval.query(from: history), "два три четыре")
    }

    func testЗапросНеДлиннее500ЗнаковИДержитСвежийХвост() {
        let long = String(repeating: "а", count: 600) + "КОНЕЦ"
        let q = ChatRetrieval.query(from: [msg("user", long)])
        XCTAssertEqual(q.count, 500)
        XCTAssertTrue(q.hasSuffix("КОНЕЦ"))
    }

    private func outcome(_ text: String, _ verdict: ArchiveSearch.Verdict,
                         note: String = "") -> ArchiveSearch.Outcome {
        ArchiveSearch.Outcome(text: text, rels: nil, verdict: verdict, note: note)
    }

    func testБлокГрафаСильный() {
        let c = ChatRetrieval.contextBlock(outcome("- Встречи/a.md\nтекст", .confident), lang: "ru")
        XCTAssertEqual(c.verdict, .confident)
        XCTAssertFalse(c.weak)
        XCTAssertEqual(c.text, "- Встречи/a.md\nтекст")
        XCTAssertTrue(c.block.hasPrefix("\n\n[ГРАФ ВСТРЕЧ — люди, системы, решения, документация]\nПоиск нашёл совпадения"))
        XCTAssertTrue(c.block.hasSuffix("\n- Встречи/a.md\nтекст"))
    }

    func testБлокГрафаСлабыйСнимаетМаркер() {
        let c = ChatRetrieval.contextBlock(outcome(ArchiveSearch.lowConfidenceMarker + "- Встречи/a.md", .weak), lang: "ru")
        XCTAssertTrue(c.weak)
        XCTAssertEqual(c.text, "- Встречи/a.md")
        XCTAssertTrue(c.block.hasPrefix("\n\n[ГРАФ ВСТРЕЧ — совпадения СЛАБЫЕ, вероятно не про вопрос; не выдавай за факт]\n"))
        XCTAssertFalse(c.block.contains(ArchiveSearch.lowConfidenceMarker))
    }

    /// Пустая выдача — тоже решение поиска: блок с правилом есть всегда,
    /// иначе модель решала бы сама по основе промпта.
    func testПустаяВыдачаДаётБлокСПравилом() {
        let empty = ChatRetrieval.contextBlock(outcome("", .empty, note: "Ничего не найдено (искали без: архив)"), lang: "ru")
        XCTAssertEqual(empty.verdict, .empty)
        XCTAssertTrue(empty.block.contains("в памяти этого нет"))
        XCTAssertTrue(empty.block.contains("(Ничего не найдено (искали без: архив))"), "оговорка поиска потеряна")
        // вопросы о разговоре не получают отказа
        XCTAssertTrue(empty.block.contains("по разговору"))
        XCTAssertTrue(empty.block.contains("где не искали"), "пропуск поиска модель не назовёт")
        let unverified = ChatRetrieval.contextBlock(outcome("", .unverified), lang: "ru")
        XCTAssertTrue(unverified.block.contains("поиск неполный"))
        // фрагменты не прошли гейт и охват не доказан: чипов нет, как до №634,
        // а строка статуса — «поиск неполный», не «слабые совпадения» (out2)
        let m = ArchiveSearch.lowConfidenceMarker
        let unverifiedText = ChatRetrieval.contextBlock(outcome(m + "- a.md\nт", .unverified), lang: "ru")
        XCTAssertTrue(unverifiedText.hidesSources)
        // подвал «слабые совпадения» — не у неполного поиска (out3, DS I6)
        XCTAssertFalse(unverifiedText.weak)
        if L.lang == "ru" { XCTAssertEqual(LocalChatService.memoryStatus(unverifiedText), "граф: поиск неполный") }
        // сильная лексика без семантики: маркера нет — чипы видны (out3, DS I3)
        XCTAssertFalse(ChatRetrieval.contextBlock(outcome("- a.md\nт", .unverified)).hidesSources)
        XCTAssertFalse(ChatRetrieval.contextBlock(outcome("- a.md\nт", .confident)).hidesSources)
        let weakText = ChatRetrieval.contextBlock(outcome(m + "- a.md\nт", .weak))
        XCTAssertTrue(weakText.weak)
        XCTAssertTrue(weakText.hidesSources)
        // оговорка сервера доходит до модели и при непустой выдаче
        let noted = ChatRetrieval.contextBlock(outcome("- a.md\nт", .weak, note: "почти ничего нет"), lang: "ru")
        XCTAssertTrue(noted.block.contains("(почти ничего нет)"))
        // «найдено» без текста не бывает
        XCTAssertEqual(ChatRetrieval.contextBlock(outcome("", .confident)).verdict, .empty)
    }

    /// Формула отсутствия — только в правилах вердиктов, где поиск её
    /// доказал (слабо, пусто), и нигде больше: ни в основе промпта, ни в
    /// правилах «нашлось», «не проверено», «недоступен» она не велит отвечать
    /// отказом. Цикл по всем вердиктам и языкам — новое значение без строки
    /// в таблице тест не пропустит (№634).
    func testФормулаОтсутствияТолькоУДоказанногоПусто() {
        // право на отказ — у проверенного «пусто» и «слабо», полем; формула
        // в тексте правила — ровно там, где поле велит
        let formula = Dictionary(uniqueKeysWithValues: ["ru", "en", "zh"].map {
            ($0, ChatRetrieval.absenceFormula(lang: $0)) })
        XCTAssertEqual(formula["ru"], "«в памяти этого нет»")
        for (lang, f) in formula {
            for v in ArchiveSearch.Verdict.allCases {
                for hasText in [true, false] {
                    let h = ChatRetrieval.honesty(v, hasText: hasText, lang: lang)
                    XCTAssertFalse(h.head.isEmpty, "\(v) \(lang): пустая шапка")
                    XCTAssertFalse(h.rule.isEmpty, "\(v) \(lang): пустое правило")
                    XCTAssertEqual(h.allowsAbsence, v == .weak || v == .empty, "\(v) \(lang): отказ велит не тот вердикт")
                    XCTAssertEqual(h.rule.contains(f), h.allowsAbsence, "\(v) \(lang): формула в правиле")
                }
            }
            XCTAssertFalse(ChatRetrieval.memoryOffBlock(lang: lang).contains(f), "\(lang): выключенная память")
        }
        for local in [true, false] {
            let base = ChatRetrieval.baseSystem(inferenceIsLocal: local)
            for f in formula.values { XCTAssertFalse(base.contains(f), "формула отсутствия в основе промпта") }
        }
    }

    /// Ответ brain-сервера разбирается в одном месте; срез по первому
    /// «\n\n» терял строку причины и пустое «не проверено» клал в блок.
    func testРазборОтветаBrainСервера() throws {
        let weak = try XCTUnwrap(ArchiveSearch.brainOutcome(ArchiveSearch.brainSlice(
            "⚠ Похоже, почти ничего нет (слабые совпадения). Ниже ближайшее найденное:\n"
            + "Найдено в vault (1 из 9):\n\n• Граф/Ядра/А.md\n  …текст…"), folder: "Граф"))
        XCTAssertEqual(weak.verdict, .weak)
        XCTAssertEqual(weak.note, "Похоже, почти ничего нет (слабые совпадения). Ниже ближайшее найденное:")
        XCTAssertEqual(weak.text, ArchiveSearch.lowConfidenceMarker + "• Граф/Ядра/А.md\n  …текст…")
        XCTAssertEqual(weak.rels, ["Ядра/А.md"])

        let confident = try XCTUnwrap(ArchiveSearch.brainOutcome(ArchiveSearch.brainSlice(
            "Найдено в vault (1 из 9):\n\n• Граф/Б.md\n  …б…"), folder: "Граф"))
        XCTAssertEqual(confident.verdict, .confident)
        XCTAssertEqual(confident.text, "• Граф/Б.md\n  …б…")

        let unverified = try XCTUnwrap(ArchiveSearch.brainOutcome(ArchiveSearch.brainSlice(
            "⚠ Найдено по словам, семантикой не проверено (кэш)\nНайдено в vault (1 из 9):\n\n• Граф/В.md\n  …в…"),
            folder: "Граф"))
        XCTAssertEqual(unverified.verdict, .unverified)
        XCTAssertEqual(unverified.rels, ["В.md"])

        // «⚠» без выдачи — пусто не доказано: решает локальный поиск
        XCTAssertNil(ArchiveSearch.brainOutcome(ArchiveSearch.brainSlice(
            "⚠ По словам ничего не нашлось по «x» в графе, семантикой не проверено (кэш) — не считать доказанным отсутствием"),
            folder: "Граф"))

        // «Ничего не найдено» сервера — не доказательство: его индекс без
        // архива встреч, решает локальный поиск (out3, C1 DS и Haiku)
        XCTAssertNil(ArchiveSearch.brainOutcome(ArchiveSearch.brainSlice(
            "Ничего не найдено по «x» в графе (искали без: архив)"), folder: "Граф"))

        // «⚠» не первой строкой шапки — вердикт и оговорка не теряются (out3, DS I5)
        let late = try XCTUnwrap(ArchiveSearch.brainOutcome(ArchiveSearch.brainSlice(
            "📋 ДОСЬЕ «X» (Граф, источников 3)\n⚠ Похоже, почти ничего нет.\nНайдено в vault (1 из 9):\n\n• Граф/А.md\n  …т…"),
            folder: "Граф"))
        XCTAssertEqual(late.verdict, .weak)
        XCTAssertEqual(late.note, "Похоже, почти ничего нет.")
    }

    func testСтатусИзВердикта() throws {
        guard L.lang == "ru" else { throw XCTSkip("строки статуса — на языке интерфейса, проверяем русские") }
        let ctx = { (t: String, v: ArchiveSearch.Verdict) in ChatRetrieval.contextBlock(self.outcome(t, v)) }
        XCTAssertEqual(LocalChatService.memoryStatus(ctx("", .empty)), "в графе пусто по теме")
        XCTAssertEqual(LocalChatService.memoryStatus(ctx("", .unverified)), "граф: поиск неполный")
        XCTAssertEqual(LocalChatService.memoryStatus(ctx("т", .unverified)), "граф: поиск неполный")
        XCTAssertEqual(LocalChatService.memoryStatus(ctx("", .unavailable)), "граф недоступен")
        XCTAssertEqual(LocalChatService.memoryStatus(ctx("т", .confident)), "🧠 граф подмешан")
        XCTAssertEqual(LocalChatService.memoryStatus(ctx("т", .weak)), "граф: слабые совпадения")
    }

    /// Детектор отказа бенча (out1, DS C1, Sonnet I3): вступление «в
    /// предоставленных блоках» — не отказ, отказ под заголовком и в другой
    /// словоформе — отказ.
    func testДетекторОтказа() {
        for answer in ["В предоставленных блоках сказано: решили перенести релиз на август.",
                       "Нет информации о сроках, но решили перенести релиз.",
                       "Решили перенести релиз.\nВ памяти этого нет: даты пока не назвали.\nИсточник: a.md",
                       // общие подстроки не отказ (out2, Sonnet I6)
                       "Расхождений в цифрах не нашлось, релиз в августе.", "No information gaps: the release is in August.",
                       "No records were lost; the release is in August."] {
            XCTAssertFalse(ChatRetrieval.isAbsence(answer), answer)
        }
        for answer in ["В памяти этого нет.", "## Ответ\n\nВ памяти этого нет.", "**Этого нет в памяти**: переезд не обсуждался.",
                       "Ответ:\nВ графе этого нет.", "I don't have information about that.", "That isn't in memory… no, it is not in memory.",
                       "没有找到相关内容。", "That is not in memory.",
                       // метка и заголовок, которые сами отказ, не пропускаются (out2, Sonnet I5)
                       "Нет в памяти:\nпереезд не обсуждали, обсуждали ремонт.", "## В графе этого нет\nЕсть про ремонт."] {
            XCTAssertTrue(ChatRetrieval.isAbsence(answer), answer)
        }
        // вопрос без ответа: найденное первой строкой, отказ второй — воздержание
        XCTAssertTrue(ChatRetrieval.isAbsence("Нашлось про релиз в августе.\nТочного ответа нет.", lines: 2))
        // ложный отказ — только формула отсутствия, а не общий оборот факта (out3, DS I4)
        for answer in ["No record of objections; the release stays in August.",
                       "No information about the outage was shared at the meeting.",
                       "Поиск был неполным: точного ответа не нашлось, но речь шла о ремонте."] {
            XCTAssertFalse(ChatRetrieval.isAbsence(answer, claimsOnly: true), answer)
        }
        XCTAssertTrue(ChatRetrieval.isAbsence("**В памяти этого нет.**", claimsOnly: true))
        XCTAssertTrue(ChatRetrieval.isAbsence("记忆中没有这条。", claimsOnly: true))
    }

    /// Ложный отказ бенча — нарушение правила вердикта, а не исполнение
    /// (out3, DS C2): «слабо» и «пусто» формулу велят, «неполный поиск» велит
    /// «точного ответа не нашлось».
    func testЛожныйОтказТолькоПротивПравила() {
        let refusal = "В памяти этого нет; рядом нашлось про ремонт."
        XCTAssertTrue(ChatRetrieval.refusesAgainstRule(refusal, verdict: .confident))
        XCTAssertTrue(ChatRetrieval.refusesAgainstRule(refusal, verdict: .unverified))
        XCTAssertFalse(ChatRetrieval.refusesAgainstRule(refusal, verdict: .weak))
        XCTAssertFalse(ChatRetrieval.refusesAgainstRule(refusal, verdict: .empty))
        XCTAssertFalse(ChatRetrieval.refusesAgainstRule(
            "Точного ответа не нашлось, поиск был неполным.", verdict: .unverified))
    }

    func testТелоЗапросаПоПолям() throws {
        let messages = [["role": "system", "content": "s"], ["role": "user", "content": "q"]]
        for (stream, temperature) in [(true, 0.35), (false, 0.0)] {
            let body = ChatRetrieval.requestBody(model: "m", messages: messages, stream: stream,
                                                 temperature: temperature)
            let data = try JSONSerialization.data(withJSONObject: body)
            let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            XCTAssertEqual(obj["model"] as? String, "m")
            XCTAssertEqual(obj["messages"] as? [[String: String]], messages)
            XCTAssertEqual(obj["stream"] as? Bool, stream)
            XCTAssertEqual(obj["think"] as? Bool, false)
            XCTAssertEqual(obj["keep_alive"] as? String, "30m")
            let options = try XCTUnwrap(obj["options"] as? [String: Any])
            XCTAssertEqual(options["temperature"] as? Double, temperature)
            XCTAssertEqual(options["num_ctx"] as? Int, 32768)
            XCTAssertEqual(options["num_predict"] as? Int, 2048)
            XCTAssertEqual(Set(obj.keys), ["model", "messages", "stream", "think", "keep_alive", "options"])
        }
    }

    /// `L.t` читает язык процесса: литералы сняты с русской ветки.
    func testОсноваПромптаИСтрокаМестаМодели() throws {
        guard L.lang == "ru" else { throw XCTSkip("литералы русской ветки, язык процесса \(L.lang)") }
        let local = ChatRetrieval.baseSystem(inferenceIsLocal: true)
        let remote = ChatRetrieval.baseSystem(inferenceIsLocal: false)
        XCTAssertTrue(local.hasPrefix("Ты — Чароит, ассистент владельца этого Mac. Отвечай по-русски"))
        XCTAssertTrue(local.hasSuffix("\nМодель отвечает на этом Mac."))
        XCTAssertTrue(remote.hasSuffix("\nМодель отвечает на другом сервере, явно выбранном в настройках; не называй этот ответ офлайн или локальным."))
        XCTAssertEqual(local.dropLast("\nМодель отвечает на этом Mac.".count),
                       remote.dropLast("\nМодель отвечает на другом сервере, явно выбранном в настройках; не называй этот ответ офлайн или локальным.".count))
    }

    /// Чат собирает ход через ChatRetrieval, а не держит свою копию: вторая
    /// копия и была причиной, по которой бенч мерил не тот путь.
    func testЧатНеДержитСвоейКопииСборки() throws {
        let src = try String(contentsOf: URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Sources/CharoiteApp/Services/LocalChatService.swift"), encoding: .utf8)
        for call in ["ChatRetrieval.baseSystem(", "ChatRetrieval.query(", "ChatRetrieval.Params.chat",
                     "ChatRetrieval.contextBlock(", "ChatRetrieval.requestBody("] {
            XCTAssertTrue(src.contains(call), "чат мимо \(call)")
        }
        for copy in ["\"keep_alive\"", "[ГРАФ ВСТРЕЧ", "limit: 8", "Ты — Чароит"] {
            XCTAssertFalse(src.contains(copy), "своя копия сборки в чате: \(copy)")
        }
    }
}
