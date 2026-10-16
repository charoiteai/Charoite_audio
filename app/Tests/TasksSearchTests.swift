import XCTest
@testable import CharoiteApp

#if os(macOS)
final class TasksSearchTests: XCTestCase {
    func testWordsCanAppearInEitherOrderAndAcrossTextAndSource() {
        XCTAssertTrue(TasksScreenPolicy.matchesSearch(text: "Мария — подписать договор", source: "Релиз",
                                                      query: "договор Мария"))
        XCTAssertTrue(TasksScreenPolicy.matchesSearch(text: "Подписать договор", source: "План Марии",
                                                      query: "Марии договор"))
        XCTAssertFalse(TasksScreenPolicy.matchesSearch(text: "Подписать договор", source: "Релиз",
                                                       query: "договор Мария"))
    }

    func testRussianYoCaseAndAccentsDoNotHideTask() {
        XCTAssertTrue(TasksScreenPolicy.matchesSearch(text: "СВЕРИТЬ ОТЧЁТ", source: "", query: "отчет"))
        XCTAssertTrue(TasksScreenPolicy.matchesSearch(text: "Review café", source: "", query: "CAFE review"))
    }

    func testWhitespaceAndChineseQueries() {
        XCTAssertTrue(TasksScreenPolicy.matchesSearch(text: "x", source: "", query: " \n\t"))
        XCTAssertTrue(TasksScreenPolicy.matchesSearch(text: "检查合同", source: "会议记录", query: "合同 会议"))
        XCTAssertFalse(TasksScreenPolicy.matchesSearch(text: "检查合同", source: "", query: "预算"))
    }

    func testTaskStatusActionIncludesTaskAndActualNextAction() {
        XCTAssertEqual(TasksScreenPolicy.statusAction(text: "Договор", done: false), "Отметить выполненным: Договор")
        XCTAssertEqual(TasksScreenPolicy.statusAction(text: "Договор", done: true), "Снова открыть поручение: Договор")
    }
}
#endif
