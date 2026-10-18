import XCTest
@testable import CharoiteApp

/// Живая проба: что реально уходит модели на вопрос по архиву.
/// Не гейт — инструмент наблюдения. Требует CHAROITE_GRAPH_DIR.
///
///   CHAROITE_GRAPH_DIR=~/путь swift test --package-path app \
///     --filter CharoiteAppLiveProbes.LiveAnswerProbe
final class LiveAnswerProbe: XCTestCase {
    func testShowWhatModelReceives() async throws {
        guard let raw = ProcessInfo.processInfo.environment["CHAROITE_GRAPH_DIR"],
              !raw.isEmpty else { throw XCTSkip("CHAROITE_GRAPH_DIR не задан") }
        let graph = URL(fileURLWithPath: (raw as NSString).expandingTildeInPath)
        let store = FileManager.default.temporaryDirectory
            .appendingPathComponent("probe-\(UUID().uuidString).bin")
        defer { try? FileManager.default.removeItem(at: store) }
        await SemanticIndex.shared.useForTests(store: store) { _ in nil }

        let params = ProbeTurn.arm.params
        let outcome = await ProbeTurn.search("что решили по витрине сертификации",
                                             params: params, root: graph)
        let out = outcome.text
        let rels = outcome.rels ?? []
        print(ProbeTurn.header("=== ЧТО ПОЛУЧАЕТ МОДЕЛЬ (\(out.count) знаков, \(rels.count) источников) ==="))
        for (i, rel) in rels.enumerated() { print("  \(i + 1). \(rel)") }
        XCTAssertFalse(out.isEmpty)
    }
}
