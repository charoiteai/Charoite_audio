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

    func testTaskFromFoundMeetingDayIsRelevant() {
        XCTAssertTrue(PrepPolicy.matchesTopic(
            text: "Мария — прислать договор",
            source: "Встречи/2026-08-01_1000.md",
            topic: "Синк по ЮPay",
            relatedDays: ["202608011000"]
        ))
    }

    func testTaskCanMatchSpecificTopicWords() {
        XCTAssertTrue(PrepPolicy.matchesTopic(
            text: "Проверить ретеншн партиций",
            source: "Задачи/платформа.md",
            topic: "Синк: ретеншн партиций"
        ))
    }

    func testGenericMeetingWordsDoNotMakeTaskRelevant() {
        XCTAssertFalse(PrepPolicy.matchesTopic(
            text: "Подготовить смету для офиса",
            source: "Задачи/общие.md",
            topic: "Еженедельный синк команды"
        ))
    }

    func testUnrelatedTaskIsNotRelevant() {
        XCTAssertFalse(PrepPolicy.matchesTopic(
            text: "Согласовать отпуск",
            source: "Люди/Мария.md",
            topic: "Ретеншн партиций"
        ))
    }
}
