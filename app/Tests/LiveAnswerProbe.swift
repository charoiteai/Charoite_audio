import XCTest
@testable import CharoiteApp

/// Живая проба: что реально уходит модели на вопрос по архиву.
/// Не гейт — инструмент наблюдения. Требует CHAROITE_GRAPH_DIR.
final class LiveAnswerProbe: XCTestCase {
    func testShowWhatModelReceives() async throws {
        guard let raw = ProcessInfo.processInfo.environment["CHAROITE_GRAPH_DIR"],
              !raw.isEmpty else { throw XCTSkip("CHAROITE_GRAPH_DIR не задан") }
        let graph = URL(fileURLWithPath: (raw as NSString).expandingTildeInPath)
        let store = FileManager.default.temporaryDirectory
            .appendingPathComponent("probe-\(UUID().uuidString).bin")
        defer { try? FileManager.default.removeItem(at: store) }
        await SemanticIndex.shared.useForTests(store: store) { _ in nil }

        let out = await ArchiveSearch.localSearch(query: "что решили по витрине сертификации",
                                                  limit: 5, snippet: 1200, root: graph)
        let sources = out.components(separatedBy: "\n\n")
        print("=== ЧТО ПОЛУЧАЕТ МОДЕЛЬ (\(out.count) знаков, \(sources.count) источников) ===")
        for (i, s) in sources.enumerated() {
            let head = s.split(whereSeparator: \.isNewline).first ?? ""
            print("  \(i + 1). \(head) — \(s.count) знаков")
        }
        XCTAssertFalse(out.isEmpty)
    }
}
