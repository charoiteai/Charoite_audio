import XCTest
@testable import CharoiteApp

/// Замер поиска на реальном графе. Не гейт (у CI графа нет) — инструмент,
/// чтобы говорить о производительности числами, а не ощущениями.
final class SearchPerfTests: XCTestCase {
    /// Граф для замера задаётся окружением: у каждого он свой, а зашивать
    /// чужой путь в публичный репозиторий нельзя.
    ///   CHAROITE_GRAPH_DIR=~/путь/к/графу swift test --filter SearchPerfTests
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

/// Байтовый счётчик обязан считать ровно то же, что считал строковый.
final class ByteCountingTests: XCTestCase {
    func testCountsMatchNaiveStringSearch() {
        let cases: [(String, String)] = [
            ("витрин", "витрина витрины ВИТРИН витринах"),
            ("ё", "ёлка ёж"),
            ("аа", "аааа"),                     // без перекрытий: 2, а не 3
            ("нет", "здесь этого слова не найти"),
            ("", "пусто"),
            ("длинная игла", "короткий"),
        ]
        for (needle, hay) in cases {
            let naive = naiveCount(of: needle, in: hay)
            let fast = ArchiveSearch.countOccurrencesForTests(of: needle, in: hay)
            XCTAssertEqual(fast, naive, "«\(needle)» в «\(hay)»: байты \(fast), строки \(naive)")
        }
    }

    private func naiveCount(of needle: String, in hay: String) -> Int {
        guard !needle.isEmpty else { return 0 }
        var count = 0
        var idx = hay.startIndex
        while let r = hay.range(of: needle, range: idx..<hay.endIndex) {
            count += 1
            idx = r.upperBound
        }
        return count
    }
}
