import XCTest
@testable import CharoiteApp

#if os(macOS)
/// Лента библиотеки по макету MOBILE_2026-08: группы по дням, сводка,
/// подписи чисел.
final class LibraryScreenPolicyTests: XCTestCase {

    // Воскресенье 23 августа 2026, 20:00 — конец недели: «на этой неделе»
    // должно уместить и понедельник 17-го.
    private var now: Date {
        var c = DateComponents(); c.year = 2026; c.month = 8; c.day = 23; c.hour = 20
        return Calendar.current.date(from: c)!
    }

    private func day(_ d: Int, month: Int = 8, hour: Int = 11) -> Date {
        var c = DateComponents(); c.year = 2026; c.month = month; c.day = d; c.hour = hour
        return Calendar.current.date(from: c)!
    }

    private var monday: Calendar {
        var cal = Calendar(identifier: .iso8601)
        cal.firstWeekday = 2
        return cal
    }

    func testBucketsFollowCalendarDayAndWeek() {
        let cal = monday
        XCTAssertEqual(LibraryScreenPolicy.bucket(of: day(23, hour: 9), now: now, calendar: cal), .today)
        XCTAssertEqual(LibraryScreenPolicy.bucket(of: day(17, hour: 8), now: now, calendar: cal), .week,
                       "понедельник той же недели — «на этой неделе»")
        XCTAssertEqual(LibraryScreenPolicy.bucket(of: day(16), now: now, calendar: cal), .earlier,
                       "воскресенье прошлой недели — «раньше»")
        XCTAssertEqual(LibraryScreenPolicy.bucket(of: day(23, hour: 23), now: now, calendar: cal), .today,
                       "позже сегодня — всё ещё «сегодня»")
        XCTAssertEqual(LibraryScreenPolicy.bucket(of: day(24, hour: 10), now: now, calendar: cal), .upcoming,
                       "завтра (уже следующая неделя) — «впереди», а не «сегодня»")
        XCTAssertEqual(LibraryScreenPolicy.bucket(of: day(20), now: day(18), calendar: cal), .week,
                       "будущее внутри этой недели — «на этой неделе»")
        XCTAssertEqual(LibraryScreenPolicy.bucket(of: day(5, month: 9), now: now, calendar: cal), .upcoming)
    }

    func testSectionsDropEmptyBucketsAndSortNewestFirst() {
        let cal = monday
        let items = [day(17), day(23, hour: 9), day(23, hour: 15), day(2, month: 7), day(9, month: 9), day(1, month: 9)]
        let sections = LibraryScreenPolicy.sections(items, date: { $0 }, now: now, calendar: cal)
        XCTAssertEqual(sections.map(\.bucket), [.upcoming, .today, .week, .earlier])
        XCTAssertEqual(sections[0].items, [day(1, month: 9), day(9, month: 9)],
                       "будущее за неделей — сверху, ближайшее первым")
        XCTAssertEqual(sections[1].items, [day(23, hour: 15), day(23, hour: 9)], "внутри секции новое первым")
        XCTAssertEqual(sections[2].items, [day(17)])
        let onlyOld = LibraryScreenPolicy.sections([day(2, month: 7)], date: { $0 }, now: now, calendar: cal)
        XCTAssertEqual(onlyOld.map(\.bucket), [.earlier], "пустые корзины не показываем")
    }

    func testSummaryCountsPipelineStates() {
        let s = LibraryScreenPolicy.summary([.ready, .processing, .error, .empty, .ready, .unknown])
        XCTAssertEqual(s, .init(total: 6, processing: 1, failed: 1))
    }

    func testRussianPluralsAndMetaSkipZeros() {
        XCTAssertEqual(LibraryScreenPolicy.participants(1), "1 участник")
        XCTAssertEqual(LibraryScreenPolicy.participants(3), "3 участника")
        XCTAssertEqual(LibraryScreenPolicy.participants(11), "11 участников")
        XCTAssertEqual(LibraryScreenPolicy.participants(21), "21 участник")
        XCTAssertEqual(LibraryScreenPolicy.tasks(5), "5 поручений")
        XCTAssertEqual(LibraryScreenPolicy.meetings(2), "2 встречи")
        XCTAssertEqual(LibraryScreenPolicy.meta(duration: "48 мин", participants: 0, tasks: 0), ["48 мин"],
                       "нули не пишем")
        XCTAssertEqual(LibraryScreenPolicy.meta(duration: nil, participants: 2, tasks: 3),
                       ["2 участника", "3 поручения"])
        XCTAssertEqual(LibraryScreenPolicy.meta(duration: "", participants: 0, tasks: 0), [])
    }
}
#endif
