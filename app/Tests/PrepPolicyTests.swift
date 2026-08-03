import XCTest
@testable import CharoiteApp

/// Чистая логика экрана подготовки: запрос в архив из названия события.
final class PrepPolicyTests: XCTestCase {

    func testPlainTitlePassesThrough() {
        XCTAssertEqual(PrepPolicy.titleQuery("Синк по проекту"), "Синк по проекту")
    }

    func testParenthesisedTailIsCut() {
        XCTAssertEqual(PrepPolicy.titleQuery("Синк по проекту (еженедельно)"),
                       "Синк по проекту")
    }

    func testOnlyFirstParenthesisMatters() {
        XCTAssertEqual(PrepPolicy.titleQuery("Разбор (перенос) (комната 4)"),
                       "Разбор")
    }

    func testSpacesCollapse() {
        XCTAssertEqual(PrepPolicy.titleQuery("  Планёрка   команды  "),
                       "Планёрка команды")
    }

    func testEmptyAndParenthesisOnlyGiveEmpty() {
        XCTAssertEqual(PrepPolicy.titleQuery(""), "")
        XCTAssertEqual(PrepPolicy.titleQuery("(бронь переговорки)"), "")
    }
}
