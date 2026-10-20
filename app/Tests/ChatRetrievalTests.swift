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

    private func outcome(_ text: String, _ verdict: ArchiveSearch.Verdict? = nil,
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
        let unverified = ChatRetrieval.contextBlock(outcome("", .unverified), lang: "ru")
        XCTAssertTrue(unverified.weak)
        XCTAssertTrue(unverified.block.contains("семантикой не проверено"))
        // «найдено» без текста не бывает
        XCTAssertEqual(ChatRetrieval.contextBlock(outcome("", .confident)).verdict, .empty)
    }

    /// Формула отсутствия — только в правилах вердиктов, где поиск её
    /// доказал (слабо, пусто), и нигде больше: ни в основе промпта, ни в
    /// правилах «нашлось», «не проверено», «недоступен» она не велит отвечать
    /// отказом. Цикл по всем вердиктам и языкам — новое значение без строки
    /// в таблице тест не пропустит (№634).
    func testФормулаОтсутствияТолькоУДоказанногоПусто() {
        let formula = ["ru": "«в памяти этого нет»", "en": "\"that is not in memory\"", "zh": "「记忆中没有这条」"]
        for (lang, f) in formula {
            for v in ArchiveSearch.Verdict.allCases {
                for hasText in [true, false] {
                    let (head, rule) = ChatRetrieval.honesty(v, hasText: hasText, lang: lang)
                    XCTAssertFalse(head.isEmpty, "\(v) \(lang): пустая шапка")
                    XCTAssertFalse(rule.isEmpty, "\(v) \(lang): пустое правило")
                    let tells = rule.hasPrefix(L.t("Поиск проверил", "The search checked", "检索已查过", lang: lang))
                        || rule.hasPrefix(L.t("О фактах встреч и людях скажи", "For facts about meetings and people, say", "关于会议和人物的事实，说", lang: lang))
                    XCTAssertEqual(tells, v == .weak || v == .empty, "\(v) \(lang): отказ велит не тот вердикт")
                    XCTAssertEqual(rule.contains(f), v != .unverified, "\(v) \(lang): формула в правиле")
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
        let weak = try XCTUnwrap(ArchiveSearch.brainOutcome(ArchiveSearch.brainBody(
            "⚠ Похоже, почти ничего нет (слабые совпадения). Ниже ближайшее найденное:\n"
            + "Найдено в vault (1 из 9):\n\n• Граф/Ядра/А.md\n  …текст…"), folder: "Граф"))
        XCTAssertEqual(weak.verdict, .weak)
        XCTAssertEqual(weak.note, "Похоже, почти ничего нет (слабые совпадения). Ниже ближайшее найденное:")
        XCTAssertEqual(weak.text, ArchiveSearch.lowConfidenceMarker + "• Граф/Ядра/А.md\n  …текст…")
        XCTAssertEqual(weak.rels, ["Ядра/А.md"])

        // досье над «⚠» и шапкой: строка «⚠» уходит в вердикт, досье — в выдачу
        let dossierFirst = try XCTUnwrap(ArchiveSearch.brainOutcome(ArchiveSearch.brainBody(
            "📋 ДОСЬЕ «Тема» (Граф, источников 2)\nсводка\n\n— — — ниже отдельные фрагменты графа — — —\n\n"
            + "⚠ Похоже, слабые совпадения:\nНайдено в vault (1 из 9):\n\n• Граф/Б.md\n  …б…"), folder: "Граф"))
        XCTAssertEqual(dossierFirst.verdict, .weak)
        XCTAssertEqual(dossierFirst.note, "Похоже, слабые совпадения:")
        XCTAssertTrue(dossierFirst.text.contains("сводка") && dossierFirst.text.contains("…б…"),
                      "досье до шапки — тоже выдача")
        XCTAssertFalse(dossierFirst.text.contains("Найдено в vault") || dossierFirst.text.contains("— — —"))

        let unverified = try XCTUnwrap(ArchiveSearch.brainOutcome(
            "⚠ Найдено по словам, семантикой не проверено (кэш)\n• Граф/В.md\n  …в…", folder: "Граф"))
        XCTAssertEqual(unverified.verdict, .unverified)

        // «⚠» без выдачи — пусто не доказано: решает локальный поиск
        XCTAssertNil(ArchiveSearch.brainOutcome(
            "⚠ По словам ничего не нашлось по «x» в графе, семантикой не проверено (кэш) — не считать доказанным отсутствием", folder: "Граф"))
    }

    func testСтатусИзВердикта() {
        guard L.lang == "ru" else { return }
        let ctx = { (t: String, v: ArchiveSearch.Verdict) in ChatRetrieval.contextBlock(self.outcome(t, v)) }
        XCTAssertEqual(LocalChatService.memoryStatus(ctx("", .empty)), "в графе пусто по теме")
        XCTAssertEqual(LocalChatService.memoryStatus(ctx("", .unverified)), "граф: поиск неполный")
        XCTAssertEqual(LocalChatService.memoryStatus(ctx("", .unavailable)), "граф недоступен")
        XCTAssertEqual(LocalChatService.memoryStatus(ctx("т", .confident)), "🧠 граф подмешан")
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
