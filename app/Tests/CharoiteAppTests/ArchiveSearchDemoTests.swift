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

    /// Языки без пробелов: иероглифы не разделяются, и запрос целиком
    /// становился одним «словом», которого в тексте нет — выдача пустая.
    /// Замер 19.08 на демо-графах: китайский 0 попаданий против 3 из 3 у
    /// английского, при том что факты в графе лежат.
    func testChineseDemoGraphFindsPaymentProvider() async {
        let demo = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("demo/graph_zh")
        let out = await ArchiveSearch.localSearch(
            query: "支付服务商最后定了哪一家？",
            limit: 5, snippet: 400, root: demo)
        XCTAssertTrue(out.contains("YuPay"), "пусто или мимо: \(out.prefix(200))")
    }

    func testChineseQueryIsCutIntoBigrams() {
        XCTAssertEqual(ArchiveSearch.cjkGrams("支付服务商"), ["支付", "付服", "服务", "务商"])
        XCTAssertEqual(ArchiveSearch.cjkGrams("九"), ["九"])
        XCTAssertTrue(ArchiveSearch.cjkGrams("YuPay contract").isEmpty,
                      "латиница режется обычным путём, сюда попадать не должна")
    }
}
