import XCTest
@testable import CharoiteApp

final class PrepTopicQueryTests: XCTestCase {
    func testGenericCalendarTitlesCannotSearchUnrelatedMeetings() {
        for title in ["Встреча", "Созвон (еженедельно)", "Weekly sync", "Meeting call", "Встреча по", "Meeting about", "会议 例会", "", "(перенос)"] {
            XCTAssertNil(PrepPolicy.searchQuery(title), title)
        }
    }

    func testSpecificTopicRetainedWithoutGenericWordsOrRecurringSuffix() {
        XCTAssertEqual(PrepPolicy.searchQuery("Встреча по бюджету (еженедельно)"), "бюджету")
        XCTAssertEqual(PrepPolicy.searchQuery("Weekly sync Release planning"), "release planning")
        XCTAssertEqual(PrepPolicy.searchQuery("会议 预算"), "预算")
    }

    func testTopicQueryStillMatchesItsTasks() throws {
        let query = try XCTUnwrap(PrepPolicy.searchQuery("Созвон Платёжная платформа"))
        XCTAssertTrue(PrepPolicy.matchesTopic(text: "Платёжная платформа — проверить доступ", source: "", topic: query))
        XCTAssertFalse(PrepPolicy.matchesTopic(text: "Другой проект — доступ", source: "", topic: query))
    }
}
