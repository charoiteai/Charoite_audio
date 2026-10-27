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

    /// Окно дня без месяца (№805): протокол подтверждает «до 28.10» речью «к 28-му»,
    /// только если дата в окне встречи — и год этой даты обязан совпасть с тем, что
    /// чип выводит из якоря. Один источник — tests/fixtures/due_window.json, его
    /// читает и pytest (fact_check.due_dates). Срок в его же день — `.soon(days: 0)`.
    func testDueWindowMatchesTheSharedTable() throws {
        struct Case: Decodable {
            let meeting: String; let date: String; let in_window: Bool; let window: [String]; let dates: [String]
        }
        struct Table: Decodable { let grace_days: Int; let cases: [Case] }
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let table = try JSONDecoder().decode(
            Table.self, from: Data(contentsOf: root.appendingPathComponent("tests/fixtures/due_window.json")))
        XCTAssertEqual(TaskDue.restatedGraceDays, table.grace_days, "люфт — одно число с протоколом")
        func ymd(_ s: String) -> (Int, Int, Int) {
            let p = s.split(separator: "-").compactMap { Int($0) }
            return (p[0], p[1], p[2])
        }
        var checked = 0
        for c in table.cases {
            XCTAssertEqual(c.window.contains(c.date), c.in_window, "\(c.meeting): \(c.date)")
            let (my, mm, md) = ymd(c.meeting)
            if !c.in_window {
                // дата вне окна протокола и для чипа не «на днях»: ни просрочки, ни
                // близкого срока на день встречи (DS M2 out2: таблица сверяла сама себя)
                let due = try XCTUnwrap(TaskDue.parse("сдать до \(c.date)"))
                XCTAssertEqual(due.status(now: date(my, mm, md), anchor: date(my, mm, md)), .later,
                               "\(c.meeting): \(c.date) вне окна")
                checked += 1
            }
            for full in c.dates {
                let (y, m, d) = ymd(full)
                let due = try XCTUnwrap(TaskDue.parse(String(format: "сдать до %02d.%02d", d, m)))
                XCTAssertTrue(c.window.contains(due.short), "\(c.meeting): \(due.short) в окне")
                XCTAssertEqual(due.status(now: date(y, m, d), anchor: date(my, mm, md)), .soon(days: 0),
                               "\(c.meeting): \(due.short) — это \(full)")
                checked += 1
            }
        }
        XCTAssertGreaterThan(checked, 0)
    }

    func testImpossibleDayHasNoStatusWithoutAnchor() throws {
        // без якоря «до 31.04» за два дня до 01.05 было «через 2 дня» (№805 out3, Haiku I6)
        let due = try XCTUnwrap(TaskDue.parse("подать до 31.04"))
        XCTAssertEqual(due.status(now: date(2026, 4, 29), anchor: nil), .later)
        XCTAssertEqual(due.status(now: date(2026, 4, 29), anchor: date(2026, 4, 29)), .later)
        let live = try XCTUnwrap(TaskDue.parse("подать до 30.04"))
        XCTAssertEqual(live.status(now: date(2026, 4, 29), anchor: nil), .soon(days: 1))
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
