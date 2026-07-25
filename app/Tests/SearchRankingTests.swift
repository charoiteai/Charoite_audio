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
