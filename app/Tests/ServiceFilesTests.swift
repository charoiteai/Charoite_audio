import XCTest
@testable import CharoiteApp

#if os(macOS)
/// Указатели папок и индекс досье — не источник чата (№750): слоты выдачи на
/// навигацию. Карта содержания остаётся источником с чипом «Карта графа» —
/// бенч навигации без неё терял обзорные факты (7/13 против 10/13).
final class ServiceFilesTests: XCTestCase {

    private func makeGraph(_ files: [String: String]) throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("graph-svc-\(UUID().uuidString)")
        for (rel, text) in files {
            let url = dir.appendingPathComponent(rel)
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                    withIntermediateDirectories: true)
            try text.write(to: url, atomically: true, encoding: .utf8)
        }
        addTeardownBlock { try? FileManager.default.removeItem(at: dir) }
        return dir
    }

    func testServiceRuleIsTheFileNameRuleOfTheSchema() {
        XCTAssertTrue(GraphFolders.isServiceFile("_MOC.md"))
        XCTAssertTrue(GraphFolders.isServiceFile("Ядра/_ЯДРА.md"))
        XCTAssertTrue(GraphFolders.isServiceFile("Досье/_ИНДЕКС.md"))
        XCTAssertTrue(GraphFolders.isServiceFile("Служебное_отчёт.md"))
        // форма сравнения схемы: регистр, ё→е, разложенный Unicode (имя с диска macOS)
        XCTAssertTrue(GraphFolders.isServiceFile("служебное_x.md"))
        XCTAssertTrue(GraphFolders.isServiceFile("СЛУЖЕБНОЕ_x.md"))
        XCTAssertTrue(GraphFolders.isServiceFile("Служебное_".decomposedStringWithCanonicalMapping + "x.md"))
        XCTAssertFalse(GraphFolders.isServiceFile("Ядра/Тема.md"))
        XCTAssertFalse(GraphFolders.isServiceFile("Системы/Карта_mockups.md"))
        // правило ИМЕНИ файла: папку с «_» оно не судит
        XCTAssertFalse(GraphFolders.isServiceFile("_Архив/Встреча.md"))
    }

    func testMapStaysAChatSourceOnlyAtTheGraphRoot() {
        XCTAssertTrue(GraphFolders.isMapFile("_MOC.md"))
        XCTAssertTrue(GraphFolders.isMapFile("_moc.md"))
        XCTAssertFalse(GraphFolders.isMapFile("Встречи/_MOC.md"))
        XCTAssertFalse(GraphFolders.isChatExcluded("_MOC.md"))
        XCTAssertTrue(GraphFolders.isChatExcluded("Встречи/_MOC.md"))
        XCTAssertTrue(GraphFolders.isChatExcluded("Ядра/_ЯДРА.md"))
        XCTAssertTrue(GraphFolders.isChatExcluded("Досье/_ИНДЕКС.md"))
        XCTAssertFalse(GraphFolders.isChatExcluded("Ядра/Тема.md"))
    }

    /// Swift-копия префиксов совпадает с литералом схемы Чароита: третий
    /// префикс в Python без правки здесь — красный тест, а не тихое расхождение.
    func testPrefixesMatchThePythonSchema() throws {
        let schema = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // Tests
            .deletingLastPathComponent()   // app
            .deletingLastPathComponent()   // репозиторий
            .appendingPathComponent("src/charoite_schema.py")
        let text = try String(contentsOf: schema, encoding: .utf8)
        let re = try NSRegularExpression(pattern: #"service_prefixes\s*=\s*\(([^)]*)\)"#)
        let m = try XCTUnwrap(re.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)),
                              "service_prefixes не найден в \(schema.path)")
        let inside = String(text[Range(m.range(at: 1), in: text)!])
        let lits = try NSRegularExpression(pattern: #""([^"]*)""#)
            .matches(in: inside, range: NSRange(inside.startIndex..., in: inside))
            .map { String(inside[Range($0.range(at: 1), in: inside)!]) }
        XCTAssertEqual(lits, GraphFolders.servicePrefixes)
    }

    func testLocalSearchLeavesFolderIndexesOutAndKeepsTheMap() async throws {
        let store = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-svc-index-\(UUID().uuidString).json")
        await SemanticIndex.shared.useForTests(store: store) { _ in nil }
        let graph = try makeGraph([
            // карта и указатель повторяют слова запроса гуще, чем узел
            "_MOC.md": "# Карта\nПлатёжный шлюз, платёжный шлюз, провайдер, провайдер.",
            "Ядра/_ЯДРА.md": "- [[Ядра/Платёжный шлюз]] — провайдер выбран",
            "Ядра/Платёжный шлюз.md": "# Платёжный шлюз\nПровайдер выбран после теста.",
        ])
        let out = await ArchiveSearch.localSearchOutcome(
            query: "платёжный шлюз провайдер", limit: 5, snippet: 400, root: graph)
        let rels = try XCTUnwrap(out.rels)
        XCTAssertEqual(Set(rels), ["_MOC.md", "Ядра/Платёжный шлюз.md"])
        XCTAssertFalse(out.text.contains("_ЯДРА.md"),
                       "указатель папки ушёл модели: \(out.text.prefix(200))")
    }

    /// :8100 отдаёт блоки указателей и пути от корня vault (опыт out1:
    /// «ИИ-граф/_MOC.md», «Ядра/_ЯДРА.md», «Служебное_*») — блок указателя
    /// уходит и из текста модели, и из чипов; карта под префиксом папки — карта.
    func testBrainBlocksOfFolderIndexesLeaveTextAndChips() {
        let text = """
        ⚠ слабые совпадения
        • g/Ядра/_ЯДРА.md
          …сводка статусов…

        • g/Люди/Мария Соколова.md
          …см. Досье/_ИНДЕКС.md…
        • g/_MOC.md
          …обзор…
        """
        let out = ArchiveSearch.brainOutcome(text, folder: "g")
        XCTAssertEqual(out.rels, ["g/Люди/Мария Соколова.md", "g/_MOC.md"])
        XCTAssertFalse(out.text.contains("_ЯДРА") || out.text.contains("сводка статусов"),
                       "блок указателя ушёл модели: \(out.text)")
        XCTAssertTrue(out.text.hasPrefix("⚠ слабые совпадения"))
        XCTAssertTrue(out.text.contains("…обзор…") && out.text.contains("…см. Досье/_ИНДЕКС.md…"))
        // одни указатели — пустой список и текст без блоков, а не nil: фолбэк
        // по телам сниппетов собрал бы чипы из упоминаний
        let only = ArchiveSearch.brainOutcome("• g/Ядра/_ЯДРА.md\n  …Ядра/Тема.md…", folder: "g")
        XCTAssertEqual(only.rels, [])
        XCTAssertFalse(only.text.contains("Ядра/Тема.md"))
        let none = ArchiveSearch.brainOutcome("текст без заголовков, Ядра/Тема.md", folder: "g")
        XCTAssertNil(none.rels)
        XCTAssertEqual(none.text, "текст без заголовков, Ядра/Тема.md")
        // карта не в корне графа — указатель
        XCTAssertEqual(ArchiveSearch.brainOutcome("• g/Встречи/_MOC.md\n  …", folder: "g").rels, [])
    }

    /// Гейт обхода судит каноническую rel: симлинк с обычным именем на
    /// указатель папки указателем и остаётся (out1, Haiku I1).
    func testSymlinkToAFolderIndexIsStillAnIndex() async throws {
        let store = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-svc-link-\(UUID().uuidString).json")
        await SemanticIndex.shared.useForTests(store: store) { _ in nil }
        let graph = try makeGraph([
            "Ядра/_ЯДРА.md": "- [[Ядра/Шлюз]] — платёжный шлюз провайдер выбран",
            "Ядра/Шлюз.md": "# Шлюз\nПлатёжный провайдер выбран.",
        ])
        try FileManager.default.createSymbolicLink(
            atPath: graph.appendingPathComponent("Ядра/Обзор.md").path,
            withDestinationPath: "_ЯДРА.md")
        let out = await ArchiveSearch.localSearchOutcome(
            query: "платёжный шлюз провайдер", limit: 5, snippet: 400, root: graph)
        XCTAssertEqual(try XCTUnwrap(out.rels), ["Ядра/Шлюз.md"])
    }

    func testFullwidthPrefixIsServiceAsInPython() {
        XCTAssertTrue(GraphFolders.isServiceFile("Ядра/\u{FF3F}ЯДРА.md"))
        XCTAssertTrue(GraphFolders.isMapFile("\u{FF3F}MOC.md"))
    }

    func testFallbackParserAndDocChipTitle() {
        let s = MemoryScreenPolicy.sources(from: "см. Ядра/_ЯДРА.md и Документация/Отчёт.md")
        XCTAssertEqual(s.map(\.rel), ["Документация/Отчёт.md"])
        XCTAssertEqual(s.first?.title, "Документ · Отчёт")
        XCTAssertEqual(MemoryScreenPolicy.Source(rel: "_MOC.md", kind: .doc).title, "Карта графа")
    }

    func testInventoryCoresSkipTheCoreIndex() throws {
        let graph = try makeGraph([
            "Ядра/Релиз.md": "---\ntype: core\n---\n# Релиз\nподпись решена",
            "Ядра/_ЯДРА.md": "- [[Ядра/Релиз|Релиз]] — подпись решена",
        ])
        let snap = GraphInventoryService.scan(graph: graph)
        XCTAssertEqual(snap.cores.map(\.name), ["Релиз"])
        XCTAssertEqual(snap.nodes, 1)
    }

    /// Счётчик колонки и блок ядер — одно правило (out1: «Служебное_» в
    /// папке узлов считалось узлом).
    func testNodeCountersUseTheSchemaRule() throws {
        let graph = try makeGraph([
            "Люди/Мария.md": "# Мария",
            "Люди/Служебное_отчёт.md": "отчёт конвейера",
            "Люди/_ЛЮДИ.md": "- [[Люди/Мария]]",
        ])
        XCTAssertEqual(GraphInventoryService.scan(graph: graph).nodes, 1)
    }

    func testMockInNameIsNotADistillate() {
        XCTAssertEqual(ArchiveSearch.roleWeightForTests("Системы/Карта_mockups.md"), 1.0)
        // карта — дистиллят, как на базе до №750
        XCTAssertEqual(ArchiveSearch.roleWeightForTests("_MOC.md"), 1.15)
    }
}
#endif
