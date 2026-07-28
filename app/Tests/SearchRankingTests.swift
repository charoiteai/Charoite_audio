import XCTest
@testable import CharoiteApp

/// Первые тесты пакета: ранжирование поиска — чистая логика без UI.
final class SearchRankingTests: XCTestCase {
    func testStemUnifiesWordForms() {
        XCTAssertEqual(ArchiveSearch.stem("встреча"), "встреч")
        XCTAssertEqual(ArchiveSearch.stem("встречах"), "встреч")
        XCTAssertEqual(ArchiveSearch.stem("встречами"), "встреч")
    }

    func testStemKeepsShortWords() {
        XCTAssertEqual(ArchiveSearch.stem("мост"), "мост")
        XCTAssertEqual(ArchiveSearch.stem("тема"), "тема")
    }

    func testNormFoldsYo() {
        XCTAssertEqual(ArchiveSearch.norm("Тёплый"), ArchiveSearch.norm("теплый"))
    }

    func testMarkdownRendersBoldWithoutAsterisks() {
        let out = MarkdownLine.render("это **важно** знать")
        XCTAssertFalse(String(out.characters).contains("**"))
        XCTAssertTrue(String(out.characters).contains("важно"))
    }

    func testMarkdownHeadingStripsHashes() {
        let out = MarkdownLine.render("## Решения")
        XCTAssertEqual(String(out.characters), "Решения")
    }
}

/// Задачи: парсинг чекбоксов и отметка пишутся в файл корректно.
@MainActor
final class TasksServiceTests: XCTestCase {
    func testScanAndToggleRoundtrip() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("Минутки.md")
        try """
        # Минутки
        ## Поручения
        - [ ] **Мария** — подписать договор — до 31.07
        - [x] **Игорь** — прислать сравнение — сделано
        обычная строка без чекбокса
        """.write(to: file, atomically: true, encoding: .utf8)

        let svc = TasksService.shared
        svc.rescan(root: dir)
        XCTAssertEqual(svc.items.count, 2)
        XCTAssertEqual(svc.openCount, 1)

        let open = try XCTUnwrap(svc.items.first { !$0.done })
        svc.toggle(open, root: dir)
        XCTAssertEqual(svc.openCount, 0)
        let text = try String(contentsOf: file, encoding: .utf8)
        XCTAssertTrue(text.contains("- [x] **Мария**"))

        // обратно в открытую
        let done = try XCTUnwrap(svc.items.first { $0.text.contains("Мария") })
        svc.toggle(done, root: dir)
        XCTAssertEqual(svc.openCount, 1)
    }
}

/// Интеграция: гибридный поиск на демо-графе из репозитория.
final class DemoGraphSearchTests: XCTestCase {
    override func setUp() async throws {
        // Пустой индекс во временном файле и эмбеддер «Ollama лежит»: тест
        // детерминирован (чистая лексика, как в CI) и не пишет эмбеддинги
        // демо-графа в настоящий индекс на машине с поднятой Ollama.
        let store = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-demo-index-\(UUID().uuidString).json")
        await SemanticIndex.shared.useForTests(store: store) { _ in nil }
    }

    private var demoGraph: URL {
        // app/Tests/… → корень репо → demo/graph
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // Tests
            .deletingLastPathComponent()   // app
            .deletingLastPathComponent()   // repo root
            .appendingPathComponent("demo/graph")
    }

    func testDecisionQuestionFindsPaymentNodes() async {
        let out = await ArchiveSearch.localSearch(
            query: "что решили по платёжному провайдеру?", limit: 5, snippet: 600,
            root: demoGraph)
        XCTAssertTrue(out.contains("платёжного провайдера") || out.contains("Платёжный шлюз"),
                      "ожидали узлы про платёжного провайдера, получили: \(out.prefix(200))")
        XCTAssertTrue(out.lowercased().contains("юpay".lowercased()),
                      "факт-ответ (ЮPay) должен попасть в сниппеты")
    }

    func testBlockerQuestionFindsBlockerNode() async {
        let out = await ArchiveSearch.localSearch(
            query: "какие блокеры сейчас?", limit: 3, snippet: 400, root: demoGraph)
        XCTAssertTrue(out.contains("Блокеры/"), "узел блокера должен быть в выдаче")
    }

    func testOffTopicGetsLowConfidenceOrEmpty() async {
        let out = await ArchiveSearch.localSearch(
            query: "рецепт борща с пампушками", limit: 3, snippet: 300, root: demoGraph)
        XCTAssertTrue(out.isEmpty || out.hasPrefix(ArchiveSearch.lowConfidenceMarker),
                      "офтопик обязан быть пустым или с «⚠», получили: \(out.prefix(120))")
    }
}

/// Персист истории ответов: лимит и roundtrip на диске.
@MainActor
final class ArchiveHistoryStoreTests: XCTestCase {
    func testAppendPersistsAndLimits() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-history-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        let store = ArchiveHistoryStore.shared
        store.clear(root: dir)
        for i in 1...55 {
            store.append(q: "вопрос \(i)", a: "ответ \(i)", root: dir)
        }
        XCTAssertEqual(store.entries.count, 50, "лимит 50 записей")
        XCTAssertEqual(store.entries.last?.q, "вопрос 55")
        XCTAssertEqual(store.entries.first?.q, "вопрос 6", "старые срезаны")

        // roundtrip: файл читается заново
        let data = try Data(contentsOf: dir.appendingPathComponent("archive_history.json"))
        let decoded = try JSONDecoder().decode([ArchiveHistoryStore.Entry].self, from: data)
        XCTAssertEqual(decoded.count, 50)
        store.clear(root: dir)
    }
}

/// Английский поиск: en-стемминг и e2e по английскому демо-графу.
final class EnglishSearchTests: XCTestCase {
    override func setUp() async throws {
        // см. DemoGraphSearchTests.setUp — те же причины
        let store = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-demo-index-\(UUID().uuidString).json")
        await SemanticIndex.shared.useForTests(store: store) { _ in nil }
    }

    func testEnglishStemming() {
        XCTAssertEqual(ArchiveSearch.stem("decided"), "decid")
        XCTAssertEqual(ArchiveSearch.stem("blockers"), "blocker")
        XCTAssertEqual(ArchiveSearch.stem("launching"), "launch")
        // русская ветка не задета
        XCTAssertEqual(ArchiveSearch.stem("встречах"), "встреч")
    }

    private var demoGraphEn: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent("demo/graph_en")
    }

    func testEnglishDecisionQuestion() async {
        let out = await ArchiveSearch.localSearch(
            query: "what did we decide about the payment provider?",
            limit: 5, snippet: 600, root: demoGraphEn)
        XCTAssertTrue(out.contains("Payment"), "payment nodes expected: \(out.prefix(200))")
        XCTAssertTrue(out.contains("YuPay"), "the factual answer must reach snippets")
    }

    func testEnglishBlockerQuestion() async {
        let out = await ArchiveSearch.localSearch(
            query: "what are the current blockers?", limit: 3, snippet: 400,
            root: demoGraphEn)
        XCTAssertTrue(out.contains("Blockers/"), "blocker node expected")
    }

    /// Гейт честности обязан молчать, когда ответ в архиве ЕСТЬ.
    ///
    /// Проверка была только обратная — «офтопик получает пометку», и она
    /// проходила тривиально. Тем временем служебные слова (what/did/the)
    /// считались в покрытие запроса, и оба канонических вопроса из README
    /// возвращались с «⚠ возможно, в архиве этого нет» плюс инструкцией
    /// синтезу не доверять найденному. Продукт объявлял безответным вопрос,
    /// ответ на который лежал в первом же сниппете.
    func testOnTopicQuestionIsNotFlaggedLowConfidence() async {
        for query in ["what did we decide about the payment provider?",
                      "what are the current blockers?"] {
            let out = await ArchiveSearch.localSearch(
                query: query, limit: 5, snippet: 600, root: demoGraphEn)
            XCTAssertFalse(out.hasPrefix(ArchiveSearch.lowConfidenceMarker),
                           "вопрос по существу помечен как безответный: \(query)")
        }
    }

    /// Служебные слова не должны попадать в иглы: они есть почти в каждом
    /// файле и занижают покрытие, из-за чего срабатывает гейт честности.
    func testStopWordsAreNotNeedles() {
        for word in ["what", "the", "did", "current", "какие", "статус"] {
            XCTAssertTrue(ArchiveSearch.isStopWord(word),
                          "«\(word)» считается значимым словом запроса")
        }
        XCTAssertFalse(ArchiveSearch.isStopWord("payment"))
        XCTAssertFalse(ArchiveSearch.isStopWord("блокер"))
    }
}
