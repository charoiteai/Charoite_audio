import XCTest
@testable import CharoiteApp

#if os(macOS)
final class LibrarySelectionTests: XCTestCase {
    private struct Record { let id: String; let date: Date }
    private var calendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        return calendar
    }
    private let day = Date(timeIntervalSince1970: 1_700_006_400)
    private func choose(_ records: [Record], current: String?, day: Date?) -> String? {
        LibraryScreenPolicy.selectedID(in: records, current: current, day: day,
                                       id: { $0.id }, date: { $0.date }, calendar: calendar)
    }

    func testDaySwitchDoesNotKeepAnUnrelatedDetail() {
        let records = [Record(id: "other", date: day.addingTimeInterval(-86_400)),
                       Record(id: "selected-day", date: day.addingTimeInterval(3600))]
        XCTAssertEqual(choose(records, current: "other", day: day), "selected-day")
    }

    func testValidDetailSurvivesARepositoryRefreshAndRemovingDayFilter() {
        let records = [Record(id: "first", date: day), Record(id: "selected", date: day.addingTimeInterval(3600))]
        XCTAssertEqual(choose(records, current: "selected", day: day), "selected")
        XCTAssertEqual(choose(records, current: "selected", day: nil), "selected")
    }

    func testDayWithoutRecordClearsTheOldDetail() {
        let records = [Record(id: "other", date: day.addingTimeInterval(-86_400))]
        XCTAssertNil(choose(records, current: "other", day: day))
    }

    func testDeletedSelectionFallsBackOnlyInsideActiveDay() {
        let records = [Record(id: "other", date: day.addingTimeInterval(-86_400)), Record(id: "today", date: day)]
        XCTAssertEqual(choose(records, current: "deleted", day: day), "today")
        XCTAssertNil(choose([], current: "deleted", day: nil))
    }
}
#endif
