import XCTest
@testable import CharoiteApp

final class ArchiveSearchDemoTests: XCTestCase {
    func testEnglishDemoGraphFindsPaymentProvider() async {
        let demo = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("demo/graph_en")
        let out = await ArchiveSearch.localSearch(
            query: "what did we decide about the payment provider?",
            limit: 5, snippet: 400, root: demo)
        print("OUT >>>", out.prefix(300))
        XCTAssertTrue(out.lowercased().contains("payment"), "пусто или мимо: \(out.prefix(200))")
    }
}
