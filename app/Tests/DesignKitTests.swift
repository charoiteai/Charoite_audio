import XCTest
@testable import CharoiteApp

#if os(macOS)
/// TaskDue: срок читается из текста поручения, год выводится по смыслу.
final class DesignKitTests: XCTestCase {
    private func date(_ y: Int, _ m: Int, _ d: Int) -> Date {
        var c = DateComponents()
        c.year = y; c.month = m; c.day = d; c.hour = 12
        return Calendar.current.date(from: c)!
    }

    func testParseReadsDueFromTaskText() throws {
        let due = try XCTUnwrap(TaskDue.parse("**Мария** — договор с ЮPay до 22.07"))
        XCTAssertEqual(due.day, 22)
        XCTAssertEqual(due.month, 7)
        XCTAssertNil(TaskDue.parse("поручение без срока"))
        XCTAssertNil(TaskDue.parse("сделать до 45.99"), "мусорная дата — не срок")
    }

    func testOverdueCountsDaysWithinTheYear() throws {
        let due = try XCTUnwrap(TaskDue.parse("отчёт до 24.07"))
        guard case .overdue(let days) = due.status(now: date(2026, 8, 4)) else {
            return XCTFail("24.07 к 04.08 — просрочка")
        }
        XCTAssertEqual(days, 11)
    }

    func testJanuaryDueSeenInAugustBelongsToNextYear() throws {
        // В тексте года нет: «до 15.01» в августе — следующий январь,
        // а не просрочка на двести дней.
        let due = try XCTUnwrap(TaskDue.parse("подать заявку до 15.01"))
        switch due.status(now: date(2026, 8, 4)) {
        case .overdue(let days):
            XCTFail("чип показал просрочку \(days) дней вместо будущего срока")
        case .soon, .later:
            break
        }
    }

    func testThesisKindReadsEmojiButStripsIt() {
        XCTAssertEqual(ThesisKind(text: "📌 Решили выпустить в пятницу").label,
                       ThesisKind.decision.label)
        XCTAssertEqual(ThesisKind.strip("📌 Решили выпустить в пятницу"),
                       "Решили выпустить в пятницу")
        XCTAssertEqual(ThesisKind(text: "⏮ Об этом говорили 24.07").label,
                       ThesisKind.memory.label)
    }
}
#endif
