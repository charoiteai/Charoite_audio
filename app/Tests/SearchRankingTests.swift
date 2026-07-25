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
