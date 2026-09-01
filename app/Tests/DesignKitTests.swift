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

    /// Живые поручения пишут срок тремя способами (замер по рабочему
    /// графу 08.08). Одна форма покрывала 1 поручение из 111.
    func testParseReadsOtherLiveDeadlineForms() throws {
        let byK = try XCTUnwrap(TaskDue.parse("встреча по ресурсам к 07.08 так и не поставлена"))
        XCTAssertEqual(byK.day, 7)
        XCTAssertEqual(byK.month, 8)
        let byDeadline = try XCTUnwrap(TaskDue.parse("собрать цифры, дедлайн 15.09"))
        XCTAssertEqual(byDeadline.day, 15)
        XCTAssertEqual(byDeadline.month, 9)
        XCTAssertEqual(try XCTUnwrap(TaskDue.parse("успеть к дедлайну 01.12")).month, 12)
    }

    /// «до конца августа» — самая частая живая форма (16 из 111), и она
    /// намеренно НЕ распознаётся: превратить её в дату можно только
    /// додумав за человека, а врущий срок хуже отсутствующего.
    func testVagueDeadlinesAreNotInvented() {
        XCTAssertNil(TaskDue.parse("подготовить справку до конца августа"))
        XCTAssertNil(TaskDue.parse("закрыть до конца недели"))
        // «довести до своих команд» — «до» без даты, не срок.
        XCTAssertNil(TaskDue.parse("Лиды: довести до своих команд, что KPI нет"))
    }

    /// Поймано живьём на рабочем графе, а не тестом: «к » совпадало с концом
    /// слова, и дата внутри фразы становилась сроком поручения.
    func testMarkerMustBeAWholeWord() {
        XCTAssertNil(TaskDue.parse("повторить установку в понедельник 10.08 тем, кто выйдет"),
                     "«понедельни|к 10.08|» — дата установки, а не дедлайн")
        XCTAssertNil(TaskDue.parse("сверить остаток 12.09 по счёту"), "дата без маркера — не срок")
        // А отдельным словом маркер по-прежнему работает.
        XCTAssertEqual(try? XCTUnwrap(TaskDue.parse("сдать к 10.08")).day, 10)
    }

    func testOverdueCountsDaysWithinTheYear() throws {
        let due = try XCTUnwrap(TaskDue.parse("отчёт до 24.07"))
        guard case .overdue(let days) = due.status(now: date(2026, 8, 4), anchor: nil) else {
            return XCTFail("24.07 к 04.08 — просрочка")
        }
        XCTAssertEqual(days, 11)
    }

    func testJanuaryDueSeenInAugustBelongsToNextYear() throws {
        // В тексте года нет: «до 15.01» в августе — следующий январь,
        // а не просрочка на двести дней.
        let due = try XCTUnwrap(TaskDue.parse("подать заявку до 15.01"))
        switch due.status(now: date(2026, 8, 4), anchor: nil) {
        case .overdue(let days):
            XCTFail("чип показал просрочку \(days) дней вместо будущего срока")
        case .soon, .later:
            break
        }
    }

}
#endif
