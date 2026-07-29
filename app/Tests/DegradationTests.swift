import XCTest
@testable import CharoiteApp

/// Что происходит, когда Ollama недоступна.
///
/// Продукт обещает мягкую деградацию: без модели эмбеддингов поиск остаётся
/// лексическим, а не молчит. Обещание стоит проверять — оно легко ломается
/// незаметно, потому что на машине разработчика Ollama всегда поднята.
final class DegradationTests: XCTestCase {
    private func graph() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-degr-\(UUID().uuidString)")
        let cores = root.appendingPathComponent("Ядра")
        try FileManager.default.createDirectory(at: cores, withIntermediateDirectories: true)
        try """
        # Витрина сертификации
        ## Статус
        Запуск перенесён на сентябрь, ответственный Ковалёв.
        """.write(to: cores.appendingPathComponent("Витрина сертификации.md"),
                  atomically: true, encoding: .utf8)
        return root
    }

    func testSearchStillWorksWithoutEmbeddings() async throws {
        let root = try graph()
        defer { try? FileManager.default.removeItem(at: root) }
        let store = FileManager.default.temporaryDirectory
            .appendingPathComponent("degr-\(UUID().uuidString).bin")
        defer { try? FileManager.default.removeItem(at: store) }
        // Эмбеддер молчит — ровно как при выключенной Ollama.
        await SemanticIndex.shared.useForTests(store: store) { _ in nil }

        let out = await ArchiveSearch.localSearch(query: "когда запуск витрины сертификации",
                                                  limit: 5, snippet: 400, root: root)
        XCTAssertTrue(out.contains("сентябрь"),
                      "без эмбеддингов поиск замолчал — обещание мягкой деградации нарушено")
    }

    func testIndexingFailureDoesNotPoisonTheIndex() async throws {
        let store = FileManager.default.temporaryDirectory
            .appendingPathComponent("degr2-\(UUID().uuidString).bin")
        defer { try? FileManager.default.removeItem(at: store) }
        await SemanticIndex.shared.useForTests(store: store) { _ in nil }

        await SemanticIndex.shared.refresh(files: [("Ядра/А.md", 1, "# А\n\nтекст узла")])

        let count = await SemanticIndex.shared.count()
        XCTAssertEqual(count, 0, "неудачная индексация записала пустышку — файл будет считаться свежим")
    }

    func testEmptyGraphDoesNotCrash() async {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-empty-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let out = await ArchiveSearch.localSearch(query: "что угодно", limit: 5,
                                                  snippet: 400, root: root)
        XCTAssertTrue(out.isEmpty)
    }

    func testMissingGraphDoesNotCrash() async {
        let out = await ArchiveSearch.localSearch(
            query: "что угодно", limit: 5, snippet: 400,
            root: URL(fileURLWithPath: "/nonexistent/graph/path"))
        XCTAssertTrue(out.isEmpty)
    }
}

/// Граф — это чужие файлы: их правят руками, синхронизируют, ломают.
/// Поиск обязан переживать любое их состояние, а не падать на первом
/// нестандартном файле.
final class MalformedInputTests: XCTestCase {
    func testWeirdFilesDoNotBreakSearch() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-weird-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }

        // Пустой файл, файл из одних переводов строк, огромная строка без
        // пробелов, битый UTF-8, файл с нулевым байтом, имя с эмодзи.
        try "".write(to: root.appendingPathComponent("пусто.md"), atomically: true, encoding: .utf8)
        try "\n\n\n".write(to: root.appendingPathComponent("переводы.md"),
                           atomically: true, encoding: .utf8)
        try String(repeating: "витринасертификации", count: 3000)
            .write(to: root.appendingPathComponent("однострочник.md"),
                   atomically: true, encoding: .utf8)
        try Data([0xFF, 0xFE, 0x00, 0x41]).write(to: root.appendingPathComponent("битый.md"))
        try "# Витрина\n\nЗапуск в сентябре."
            .write(to: root.appendingPathComponent("встреча 🚀 итоги.md"),
                   atomically: true, encoding: .utf8)

        let store = FileManager.default.temporaryDirectory
            .appendingPathComponent("weird-\(UUID().uuidString).bin")
        defer { try? FileManager.default.removeItem(at: store) }
        await SemanticIndex.shared.useForTests(store: store) { _ in nil }

        let out = await ArchiveSearch.localSearch(query: "когда запуск витрины",
                                                  limit: 5, snippet: 400, root: root)
        XCTAssertTrue(out.contains("сентябре"),
                      "нормальный файл потерялся среди битых:\n\(out)")
    }

    func testChunkerSurvivesPathologicalInput() {
        let cases = [
            "",
            "\n\n\n",
            "#",
            "###### ",
            String(repeating: "#", count: 100),
            String(repeating: "а", count: 50_000),          // одно слово на 50 КБ
            "```\n" + String(repeating: "код\n", count: 5000),   // незакрытый блок кода
            "# \u{0}заголовок с нулевым байтом",
        ]
        for text in cases {
            let chunks = Chunker.chunks(of: text, title: "файл")
            for c in chunks {
                XCTAssertLessThan(c.embeddingText.count, 12_000,
                                  "блок длиннее предела эмбеддера на входе: \(text.prefix(20))…")
            }
        }
    }
}
