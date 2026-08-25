import XCTest
@testable import CharoiteApp

/// Разовое построение семантического индекса на настоящем графе.
///
/// Не тест, а инструмент: приложение строит индекс фоном по 48 блоков за
/// поиск, и на 4785 блоках это сотня запросов. Здесь то же самое делается за
/// один заход, настоящей Ollama и в настоящий файл индекса.
///
///   CHAROITE_BUILD_INDEX=1 CHAROITE_GRAPH_DIR=~/путь swift test \
///     --package-path app --filter CharoiteAppLiveProbes.BuildRealIndex
final class BuildRealIndex: XCTestCase {
    func testBuild() async throws {
        guard ProcessInfo.processInfo.environment["CHAROITE_BUILD_INDEX"] == "1",
              let raw = ProcessInfo.processInfo.environment["CHAROITE_GRAPH_DIR"],
              !raw.isEmpty else { throw XCTSkip("не запрошено") }
        let graph = URL(fileURLWithPath: (raw as NSString).expandingTildeInPath)

        // Собираем файлы так же, как это делает поиск: с дедупом по содержимому.
        var files: [(path: String, mtime: Double, text: String)] = []
        var seen = Set<Int>()
        let urls = (FileManager.default.enumerator(at: graph,
                        includingPropertiesForKeys: [.contentModificationDateKey],
                        options: [.skipsHiddenFiles])?.compactMap { $0 as? URL } ?? [])
            .filter { $0.pathExtension == "md" }
            .sorted { a, b in
                let aArch = a.path.contains("/Встречи-архив/")
                let bArch = b.path.contains("/Встречи-архив/")
                return aArch == bArch ? a.path < b.path : !aArch
            }
        for url in urls {
            guard let text = try? String(contentsOf: url, encoding: .utf8),
                  seen.insert(text.hashValue).inserted else { continue }
            let canon = url.resolvingSymlinksInPath().path
            let rel = canon.hasPrefix(graph.path + "/")
                ? String(canon.dropFirst(graph.path.count + 1)) : url.lastPathComponent
            let mtime = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?
                .contentModificationDate?.timeIntervalSince1970 ?? 0
            files.append((rel, mtime, text))
        }
        print("INDEX: файлов к индексации \(files.count)")

        let t0 = Date()
        await SemanticIndex.shared.refresh(files: files)
        let dt = Date().timeIntervalSince(t0)
        let indexed = await SemanticIndex.shared.count()
        let chunks = await SemanticIndex.shared.totalChunks()
        print("INDEX: готово за \(Int(dt)) с — файлов \(indexed), блоков \(chunks)")
        XCTAssertGreaterThan(indexed, 0, "индекс пуст — Ollama недоступна или нет модели bge-m3")
    }
}
