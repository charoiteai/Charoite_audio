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

    func testБлокГрафаСильный() {
        let c = ChatRetrieval.contextBlock(vault: "- Встречи/a.md\nтекст")
        XCTAssertFalse(c.weak)
        XCTAssertEqual(c.text, "- Встречи/a.md\nтекст")
        XCTAssertEqual(c.block, "\n\n[ГРАФ ВСТРЕЧ — люди, системы, решения, документация]\n- Встречи/a.md\nтекст")
    }

    func testБлокГрафаСлабыйСнимаетМаркер() {
        let c = ChatRetrieval.contextBlock(vault: ArchiveSearch.lowConfidenceMarker + "- Встречи/a.md")
        XCTAssertTrue(c.weak)
        XCTAssertEqual(c.text, "- Встречи/a.md")
        XCTAssertEqual(c.block, "\n\n[ГРАФ ВСТРЕЧ — совпадения СЛАБЫЕ, вероятно не про вопрос; не выдавай за факт]\n- Встречи/a.md")
    }

    func testПустаяВыдачаНеДаётБлока() {
        XCTAssertEqual(ChatRetrieval.contextBlock(vault: "").block, "")
        let weakEmpty = ChatRetrieval.contextBlock(vault: ArchiveSearch.lowConfidenceMarker)
        XCTAssertTrue(weakEmpty.weak)
        XCTAssertEqual(weakEmpty.block, "")
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
