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

    /// Значения — те же, что у прототипа на Python, сверенного с токенизатором
    /// Qwen3.6-35B по 11 097 заметкам графа (оценка/факт ≥ 1,18).
    func testОценкаПоПравиламПретокенизатора() {
        XCTAssertEqual(ChatRetrieval.estimatedTokens(""), 0)
        XCTAssertEqual(ChatRetrieval.estimatedTokens("abc"), 1)          // латиница ⅓, прогон — не меньше токена
        XCTAssertEqual(ChatRetrieval.estimatedTokens("2026"), 4)         // цифра — токен
        XCTAssertEqual(ChatRetrieval.estimatedTokens("абвг"), 2)         // кириллица ½
        XCTAssertEqual(ChatRetrieval.estimatedTokens("支付服务"), 4)      // BMP-иероглиф — 1
        XCTAssertEqual(ChatRetrieval.estimatedTokens("𠀀𠀁"), 8)          // редкий иероглиф — 4
        XCTAssertEqual(ChatRetrieval.estimatedTokens("1 2 3"), 5)        // пробел перед цифрой — свой токен (факт 5)
        XCTAssertEqual(ChatRetrieval.estimatedTokens("и в к"), 4)        // однобуквенное слово — токен (факт 3)
        XCTAssertEqual(ChatRetrieval.estimatedTokens("QmFzZTY0"), 5)     // смешанный регистр в слове — ½
        XCTAssertEqual(ChatRetrieval.estimatedTokens("a\n\nb"), 4)       // перевод строки — токен
        XCTAssertEqual(ChatRetrieval.estimatedTokens("📋"), 4)
        // смешанный регистр дорожает только у латиницы, иероглифы в прогоне — по 1: оценка 14 при факте 8 —
        // цена верхней границы на смешанном CJK (out2, Haiku I2; out3, DS M3)
        XCTAssertEqual(ChatRetrieval.estimatedTokens("这个版本在macOS上运行得很好"), 14)
        // двухбайтный знак — токен: «», ×, неразрывный пробел (факт 6 и 3; out2, Sonnet M3)
        XCTAssertEqual(ChatRetrieval.estimatedTokens("«да» × 2"), 7)
        XCTAssertEqual(ChatRetrieval.estimatedTokens("a\u{00A0}b"), 3)
        // комбинирующий знак — часть буквы: разложенная «й» стоит как собранная (out3, DS M4)
        XCTAssertEqual(ChatRetrieval.estimatedTokens(String(repeating: "и\u{0306}", count: 3)),
                       ChatRetrieval.estimatedTokens("ййй"))
        // замер токенизатором: редкий иероглиф 3,5 токена, «знак = токен» занижал
        XCTAssertGreaterThanOrEqual(ChatRetrieval.estimatedTokens(String(repeating: "𠀀", count: 100)), 350)
        // цифры через пробел: прежнее правило давало 0,67 от факта (out1, DS C1)
        let digits = (0..<3_000).map { String($0 % 10) }.joined(separator: " ")
        XCTAssertGreaterThanOrEqual(ChatRetrieval.estimatedTokens(digits), 5_999)
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
        let history = ChatRetrieval.historyWindow([.init(role: "user", text: String(repeating: "历", count: 9_000)),
                                                   .init(role: "assistant", text: "答")],
                                                  prompt: prompt, system: system)
        XCTAssertEqual(history.last?.text, prompt)
        let fitted = ChatRetrieval.fit(outcome(parts), system: system, turn: history)
        let memory = ChatRetrieval.contextBlock(fitted).block
        XCTAssertLessThanOrEqual(turnTokens(system: system, block: memory, history: history),
                                 ChatRetrieval.Window.input)
        XCTAssertFalse(fitted.parts?.isEmpty ?? true, "вопрос в окне — хоть один кусок памяти должен остаться")
    }

    /// Полная выдача не съедает диалог: история считается до памяти, память
    /// режется под неё (out1: Sonnet I1, DS I3, Haiku I-1 — при «истории —
    /// остаток» второму вопросу оставалось 0–1,5 тыс. токенов).
    func testПолнаяВыдачаОставляетИсторию() {
        let turns = (0..<40).flatMap { i -> [ChatContextWindow.Entry] in
            [.init(role: "user", text: "вопрос \(i) " + String(repeating: "к", count: 600)),
             .init(role: "assistant", text: "ответ \(i) " + String(repeating: "о", count: 600))]
        }
        let window = ChatRetrieval.historyWindow(turns, prompt: "а по второму пункту?", system: system)
        let spent = window.dropLast().reduce(0) { $0 + ChatRetrieval.messageTokens($1.text) }
        XCTAssertGreaterThan(spent, 5_000, "истории досталось \(spent) токенов")
        let parts = (0..<40).map { Part(text: block("Встречи/\($0).md", 3_000), rel: "Встречи/\($0).md", rank: $0) }
        let fitted = ChatRetrieval.fit(outcome(parts), system: system, turn: window)
        let memory = ChatRetrieval.contextBlock(fitted).block
        XCTAssertFalse(fitted.parts?.isEmpty ?? true)
        XCTAssertLessThanOrEqual(turnTokens(system: system, block: memory, history: window), ChatRetrieval.Window.input)
        // модель знает, что видит не всё
        XCTAssertTrue(fitted.note.contains("из 40 кусков"), fitted.note)
        XCTAssertTrue(memory.contains("из 40 кусков"))
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
        // длинный вопрос: истории — четверть остатка, три четверти — памяти
        let long = String(repeating: "д", count: 40_000)
        let free = ChatRetrieval.Window.input - ChatRetrieval.messageTokens(system) - ChatRetrieval.messageTokens(long)
        XCTAssertLessThan(free / 4, ChatRetrieval.Window.history)
        let tight = ChatRetrieval.historyWindow(turns, prompt: long, system: system)
        let tightSpent = tight.dropLast().reduce(0) { $0 + ChatRetrieval.messageTokens($1.text) }
        XCTAssertGreaterThan(tightSpent, 0)
        XCTAssertLessThanOrEqual(tightSpent, free / 4)
    }

    /// Заметка о срезе бюджетом считает совпадения: сосед по ссылке (score 0)
    /// в хвосте — не «не вошедшее найденное» (out3, DS I1).
    func testЗаметкаСрезаСчитаетТолькоСовпадения() {
        let body = String(repeating: "к", count: 440)
        let hit = { (rel: String, score: Double) in ArchiveSearch.Hit(score: score, rel: rel, block: "• \(rel)\n  \(body)") }
        let neighbor = ArchiveSearch.packWithGateSlot([hit("a.md", 2), hit("b.md", 1), hit("n.md", 0)],
                                                      gateRel: nil, budget: 1_000)
        XCTAssertEqual(neighbor.kept.map(\.rel), ["a.md", "b.md"])
        XCTAssertEqual(neighbor.note, "")
        let real = ArchiveSearch.packWithGateSlot([hit("a.md", 3), hit("b.md", 2), hit("c.md", 1)],
                                                  gateRel: nil, budget: 1_000)
        XCTAssertTrue(real.note.contains("2") && real.note.contains("3"), real.note)
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
