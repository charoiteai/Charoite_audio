import XCTest
@testable import CharoiteApp

/// Замер поиска на реальном графе. Не гейт (у CI графа нет) — инструмент,
/// чтобы говорить о производительности числами, а не ощущениями.
final class SearchPerfProbe: XCTestCase {
    /// Граф для замера задаётся окружением: у каждого он свой, а зашивать
    /// чужой путь в публичный репозиторий нельзя.
    ///   CHAROITE_GRAPH_DIR=~/путь/к/графу swift test --package-path app \
    ///     --filter CharoiteAppLiveProbes.SearchPerfProbe
    private var realGraph: URL? {
        guard let raw = ProcessInfo.processInfo.environment["CHAROITE_GRAPH_DIR"],
              !raw.isEmpty else { return nil }
        let p = URL(fileURLWithPath: (raw as NSString).expandingTildeInPath)
        return FileManager.default.fileExists(atPath: p.path) ? p : nil
    }

    func testSearchLatencyOnRealGraph() async throws {
        guard let graph = realGraph else {
            throw XCTSkip("CHAROITE_GRAPH_DIR не задан — замер пропущен")
        }
        let store = FileManager.default.temporaryDirectory
            .appendingPathComponent("perf-\(UUID().uuidString).bin")
        defer { try? FileManager.default.removeItem(at: store) }
        // Семантику отключаем: меряем именно лексический проход по графу.
        await SemanticIndex.shared.useForTests(store: store) { _ in nil }

        for query in ["что решили по витрине", "пилот LLM статус", "блокеры по доступу"] {
            let t0 = Date()
            let out = await ArchiveSearch.localSearch(query: query, limit: 5,
                                                     snippet: 1200, root: graph)
            let dt = Date().timeIntervalSince(t0)
            print("PERF «\(query)»: \(String(format: "%.2f", dt)) с, выдача \(out.count) знаков")
            XCTAssertLessThan(dt, 30, "поиск дольше 30 секунд — непригодно")
        }
    }
}
