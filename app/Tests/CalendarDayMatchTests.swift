import XCTest
@testable import CharoiteApp

/// Матчинг записей архива с событиями календаря — чистая логика,
/// EventKit сюда не заходит.
final class CalendarDayMatchTests: XCTestCase {
    private func date(_ h: Int, _ m: Int) -> Date {
        Calendar.current.date(from: DateComponents(year: 2026, month: 8, day: 4, hour: h, minute: m))!
    }

    private func event(_ id: String, start: Date, end: Date?) -> CalendarService.DayEvent {
        CalendarService.DayEvent(id: id, title: id, start: start, end: end, attendees: 2)
    }

    func testРаннийСтартЗаписиПрикрепляетсяКСобытию() {
        // Запись включили за пять минут до начала — это та же встреча.
        let board = CalendarDayMatch.board(
            events: [event("standup", start: date(11, 30), end: date(12, 0))],
            recordStarts: [("rec-1", date(11, 25))])
        XCTAssertEqual(board.slots[0].recordIDs, ["rec-1"])
        XCTAssertTrue(board.looseRecordIDs.isEmpty)
    }

    func testДвеВстречиПодрядНеДелятЗапись() {
        // Запись 12:03 обязана уйти к событию 12:00, а не к 11:00,
        // даже если первое событие ещё «шло» по календарю.
        let board = CalendarDayMatch.board(
            events: [
                event("first", start: date(11, 0), end: date(12, 10)),
                event("second", start: date(12, 0), end: date(13, 0)),
            ],
            recordStarts: [("rec-1", date(12, 3))])
        XCTAssertTrue(board.slots[0].recordIDs.isEmpty)
        XCTAssertEqual(board.slots[1].recordIDs, ["rec-1"])
    }

    func testЗаписьБезСобытияСтановитсяСиротой() {
        // Разговор в коридоре: календарь молчал, запись есть.
        let board = CalendarDayMatch.board(
            events: [event("planning", start: date(10, 0), end: date(10, 30))],
            recordStarts: [("rec-1", date(15, 40))])
        XCTAssertTrue(board.slots[0].recordIDs.isEmpty)
        XCTAssertEqual(board.looseRecordIDs, ["rec-1"])
    }

    func testСобытиеБезКонцаПолучаетПолучасовоеОкно() {
        let board = CalendarDayMatch.board(
            events: [event("no-end", start: date(14, 0), end: nil)],
            recordStarts: [("in", date(14, 25)), ("out", date(14, 50))])
        XCTAssertEqual(board.slots[0].recordIDs, ["in"])
        XCTAssertEqual(board.looseRecordIDs, ["out"])
    }

    func testДвеЗаписиОднойВстречиОбеПрикрепляются() {
        // Запись прервалась и была включена заново — обе половины у события.
        let board = CalendarDayMatch.board(
            events: [event("long", start: date(9, 0), end: date(10, 30))],
            recordStarts: [("part-1", date(9, 1)), ("part-2", date(9, 47))])
        XCTAssertEqual(board.slots[0].recordIDs, ["part-1", "part-2"])
    }
}
