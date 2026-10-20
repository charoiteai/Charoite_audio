import XCTest
@testable import CharoiteApp

/// Один бюджет окна на ход (№743): системный промпт, блок графа, история и
/// вопрос вместе не длиннее `num_ctx − num_predict`, память — целыми кусками.
final class ChatTurnWindowTests: XCTestCase {
    private typealias Part = ArchiveSearch.Part
    private let system = ChatRetrieval.baseSystem(inferenceIsLocal: true)

    /// Сумма хода в токенах оценки — то, что уйдёт в Ollama.
    private func turnTokens(system: String, block: String, history: [ChatContextWindow.Entry]) -> Int {
        history.reduce(ChatRetrieval.messageTokens(system + block)) { $0 + ChatRetrieval.messageTokens($1.text) }
    }

    private func outcome(_ parts: [Part], verdict: ArchiveSearch.Verdict = .confident,
                         marker: Bool = false) -> ArchiveSearch.Outcome {
        ArchiveSearch.Outcome(text: (marker ? ArchiveSearch.lowConfidenceMarker : "")
                                  + parts.map(\.text).joined(separator: "\n\n"),
                              rels: parts.compactMap(\.rel), verdict: verdict, parts: parts)
    }

    private func block(_ rel: String, _ chars: Int, _ letter: Character = "а") -> String {
        "• \(rel)\n  …" + String(repeating: letter, count: chars) + "…"
    }

    func testОценкаПоДлинеЗнакаВUTF8() {
        XCTAssertEqual(ChatRetrieval.estimatedTokens(""), 0)
        XCTAssertEqual(ChatRetrieval.estimatedTokens("abc"), 1)          // ⅓ на букву
        XCTAssertEqual(ChatRetrieval.estimatedTokens("2026"), 4)         // цифра — токен
        XCTAssertEqual(ChatRetrieval.estimatedTokens("абвг"), 2)         // кириллица ½
        XCTAssertEqual(ChatRetrieval.estimatedTokens("支付服务"), 4)      // BMP-иероглиф — 1
        XCTAssertEqual(ChatRetrieval.estimatedTokens("𠀀𠀁"), 8)          // редкий иероглиф — 4
        // замер токенизатором: редкий иероглиф 3,5 токена, «знак = токен» занижал
        XCTAssertGreaterThanOrEqual(ChatRetrieval.estimatedTokens(String(repeating: "𠀀", count: 100)), 350)
    }

    func testВыдачаВОкнеНеМеняется() {
        let o = outcome([Part(text: block("Встречи/a.md", 500), rel: "Встречи/a.md", rank: 0)])
        let fitted = ChatRetrieval.fit(o, system: system, prompt: "вопрос")
        XCTAssertEqual(fitted.text, o.text)
        XCTAssertEqual(fitted.rels, o.rels)
    }

    func testКускиВыбрасываютсяЦеликомСоСлабейшего() {
        // порядок текста как у packContext: лучший первым, второй — последним
        let ranks = [0] + Array(2..<40) + [1]
        let parts = ranks.map { r in Part(text: block("Встречи/\(r).md", 3_000), rel: "Встречи/\(r).md", rank: r) }
        let o = outcome(parts)
        let fitted = ChatRetrieval.fit(o, system: system, prompt: "что решили")
        let kept = fitted.parts ?? []
        XCTAssertFalse(kept.isEmpty)
        XCTAssertLessThan(kept.count, parts.count)
        // слабейшие ушли, сильнейшие (0 и 1) на месте, порядок текста прежний
        XCTAssertEqual(kept.map(\.rank), ranks.filter { $0 < kept.count })
        XCTAssertEqual(kept.first?.rank, 0)
        XCTAssertEqual(kept.last?.rank, 1)
        // ни одного огрызка: каждый кусок — целиком из исходной выдачи
        for p in kept { XCTAssertTrue(o.text.contains(p.text)) }
        XCTAssertEqual(fitted.text, kept.map(\.text).joined(separator: "\n\n"))
        XCTAssertEqual(fitted.rels, kept.compactMap(\.rel))
        XCTAssertEqual(fitted.verdict, .confident)
        let memory = ChatRetrieval.contextBlock(fitted).block
        XCTAssertLessThanOrEqual(turnTokens(system: system, block: memory, history: [])
                                 + ChatRetrieval.messageTokens("что решили"), ChatRetrieval.Window.input)
    }

    func testМаркерСлабыхСовпаденийОстаётсяПослеВыброса() {
        let parts = (0..<40).map { Part(text: block("Ядра/\($0).md", 3_000), rel: "Ядра/\($0).md", rank: $0) }
        let fitted = ChatRetrieval.fit(outcome(parts, verdict: .weak, marker: true), system: system, prompt: "q")
        XCTAssertTrue(fitted.text.hasPrefix(ArchiveSearch.lowConfidenceMarker))
        XCTAssertTrue(ChatRetrieval.contextBlock(fitted).hidesSources)
        XCTAssertEqual(fitted.verdict, .weak)
    }

    /// Sonnet C1 = DS C2 (in2): длинный вопрос на CJK и память 25 000 знаков
    /// переполняли окно — Ollama срезала начало, системный промпт.
    func testCJKВопросИПамятьНеПереполняютОкно() {
        let prompt = String(repeating: "会", count: ChatContextWindow.maxPrompt)
        let parts = (0..<10).map { Part(text: block("Встречи/\($0).md", 2_500, "议"), rel: "Встречи/\($0).md", rank: $0) }
        let fitted = ChatRetrieval.fit(outcome(parts), system: system, prompt: prompt)
        let memory = ChatRetrieval.contextBlock(fitted).block
        let history = ChatRetrieval.historyWindow([.init(role: "user", text: String(repeating: "历", count: 9_000)),
                                                   .init(role: "assistant", text: "答")],
                                                  prompt: prompt, system: system + block)
        XCTAssertEqual(history.last?.text, prompt)
        XCTAssertLessThanOrEqual(turnTokens(system: system, block: memory, history: history),
                                 ChatRetrieval.Window.input)
        XCTAssertFalse(fitted.parts?.isEmpty ?? true, "вопрос в окне — хоть один кусок памяти должен остаться")
    }

    /// DS C1 = Sonnet I2 (in2): brain-путь бюджета не знает — 25×1000 отдаёт
    /// 76–88 тыс. знаков; окно режет его по кускам, досье — последними.
    func testBrainВыдачаПодОкном() throws {
        func dossier(_ name: String) -> String {
            "\(ArchiveSearch.dossierHead) «\(name)» (граф, источников 9, собрано 2026-10-18)\n# Досье: \(name)\n\n## Сейчас\n"
                + String(repeating: "д", count: 3_400)
        }
        var server = dossier("Первое") + "\n\n" + dossier("Второе") + "\n\nНайдено в vault (25 из 3186):\n\n"
        for i in 0..<25 {
            server += "• граф/Встречи/2026-10-\(String(format: "%02d", i + 1))_1000.md\n  "
                + String(repeating: "в", count: 3_000) + "\n\n"
        }
        XCTAssertGreaterThan(server.count, 76_000)
        let o = try XCTUnwrap(ArchiveSearch.brainOutcome(server, folder: "граф"))
        XCTAssertEqual(o.parts?.count, 27)
        XCTAssertEqual(o.parts?.prefix(2).map(\.rel), [nil, nil])
        let fitted = ChatRetrieval.fit(o, system: system, prompt: "что решили по пилоту")
        let kept = try XCTUnwrap(fitted.parts)
        XCTAssertLessThan(kept.count, 27)
        XCTAssertTrue(kept[0].text.hasPrefix(ArchiveSearch.dossierHead), "досье сильнее блоков")
        XCTAssertEqual(fitted.rels, kept.compactMap(\.rel))
        XCTAssertEqual(fitted.rels?.first, "Встречи/2026-10-01_1000.md")
        let memory = ChatRetrieval.contextBlock(fitted).block
        XCTAssertLessThanOrEqual(turnTokens(system: system, block: memory, history: [])
                                 + ChatRetrieval.messageTokens("что решили по пилоту"), ChatRetrieval.Window.input)
    }

    /// Не уместился ни один кусок — «поиск неполный», а не «в памяти этого нет».
    func testНиОдногоКускаНеОтказВПамяти() {
        let huge = Part(text: block("Встречи/a.md", 200_000), rel: "Встречи/a.md", rank: 0)
        let fitted = ChatRetrieval.fit(outcome([huge]), system: system, prompt: "q")
        XCTAssertEqual(fitted.text, "")
        XCTAssertEqual(fitted.rels, [])
        XCTAssertEqual(fitted.verdict, .unverified)
        XCTAssertTrue(fitted.note.contains("окно модели"))
        let ctx = ChatRetrieval.contextBlock(fitted)
        XCTAssertFalse(ChatRetrieval.honesty(ctx.verdict, hasText: false).allowsAbsence)
        XCTAssertTrue(ctx.block.contains("окно модели"))
    }

    func testВыдачаБезКусковСудитсяОднимКуском() {
        let small = ArchiveSearch.Outcome(text: "• a.md\n  …x…", rels: nil, verdict: .confident)
        XCTAssertEqual(ChatRetrieval.fit(small, system: system, prompt: "q").text, small.text)
        let big = ArchiveSearch.Outcome(text: String(repeating: "ы", count: 200_000), rels: nil, verdict: .confident)
        let fitted = ChatRetrieval.fit(big, system: system, prompt: "q")
        XCTAssertEqual(fitted.text, "")
        XCTAssertNil(fitted.rels)
    }

    func testИсторияБерётОстатокНоНеБольшеПотолка() {
        let turns = (0..<200).flatMap { i -> [ChatContextWindow.Entry] in
            [.init(role: "user", text: "вопрос \(i) " + String(repeating: "к", count: 300)),
             .init(role: "assistant", text: "ответ \(i) " + String(repeating: "о", count: 300))]
        }
        let roomy = ChatRetrieval.historyWindow(turns, prompt: "дальше", system: system)
        let spent = roomy.dropLast().reduce(0) { $0 + ChatRetrieval.messageTokens($1.text) }
        XCTAssertGreaterThan(spent, 0)
        XCTAssertLessThanOrEqual(spent, ChatRetrieval.Window.history)
        // свежие ходы целиком: последняя пара — самый свежий ход, вопрос — последним
        XCTAssertEqual(roomy.last?.text, "дальше")
        XCTAssertTrue(roomy.dropLast().last?.text.hasPrefix("ответ 199") ?? false)
        // система съела окно — истории нет, вопрос есть
        let full = system + String(repeating: "п", count: 2 * ChatRetrieval.Window.input)
        XCTAssertEqual(ChatRetrieval.historyWindow(turns, prompt: "дальше", system: full).map(\.text), ["дальше"])
    }

    func testPackContextОтдаётКускиСРангомДоПерестановки() {
        let hits = (0..<4).map { ArchiveSearch.Hit(score: Double(10 - $0), rel: "r\($0).md", block: "• r\($0).md\n  …x…") }
        let packed = ArchiveSearch.packContextKept(hits, budget: 6000)
        XCTAssertEqual(packed.parts.map(\.rel), ["r0.md", "r2.md", "r3.md", "r1.md"])
        XCTAssertEqual(packed.parts.map(\.rank), [0, 2, 3, 1])
        XCTAssertEqual(packed.text, packed.parts.map(\.text).joined(separator: "\n\n"))
    }

    func testЗапросТелаБерётОкноИзОднойТаблицы() throws {
        let body = ChatRetrieval.requestBody(model: "m", messages: [], stream: false)
        let options = try XCTUnwrap(body["options"] as? [String: Any])
        XCTAssertEqual(options["num_ctx"] as? Int, ChatRetrieval.Window.context)
        XCTAssertEqual(options["num_predict"] as? Int, ChatRetrieval.Window.answer)
    }

    /// Бенч мерит выдачу под окном, как в чате.
    func testПробаИдётЧерезОкно() throws {
        let src = try String(contentsOf: URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Probes/ProbeTurn.swift"), encoding: .utf8)
        XCTAssertTrue(src.contains("ChatRetrieval.fit("))
    }
}
