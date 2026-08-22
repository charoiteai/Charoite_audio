import XCTest
@testable import CharoiteApp

#if os(macOS)
/// «Что вы обещали» на экране подготовки: сводка по корзинам срока и три
/// самых горящих поручения. Ревизия 08.08 приводила «25 в общем списке»
/// как пример числа без срока — здесь закрепляется, что у каждого числа
/// есть корзина, а порядок строк — по горению, не по файлу.
final class PrepDebtsTests: XCTestCase {
    private func date(_ y: Int, _ m: Int, _ d: Int) -> Date {
        var c = DateComponents()
        c.year = y; c.month = m; c.day = d; c.hour = 12
        return Calendar.current.date(from: c)!
    }

    private func item(_ text: String, line: Int) -> TasksService.Item {
        TasksService.Item(id: "f#\(line)", file: URL(fileURLWithPath: "/tmp/f.md"), rel: "f.md",
                          lineIndex: line, text: text, done: false,
                          fileDate: date(2026, 8, 1), sourceLine: "- [ ] \(text)")
    }

    func testSummaryNamesEveryBucketAndSkipsZeros() {
        let now = date(2026, 8, 4)
        let summary = PrepView.debtsSummary(
            ["отчёт до 24.07", "созвон к 06.08", "собрать цифры без даты"], now: now)
        XCTAssertEqual(summary, "1 просрочено · 1 на этой неделе · 1 без срока")
        XCTAssertEqual(PrepView.debtsSummary(["без срока"], now: now), "1 без срока",
                       "нули не пишем — строка не должна превращаться в «0 просрочено · …»")
    }

    func testMostUrgentPutsOldestOverdueFirstThenWeekThenUndated() {
        let now = date(2026, 8, 4)
        let items = [
            item("позже — до 20.12", line: 1),
            item("без срока", line: 2),
            item("неделя — к 06.08", line: 3),
            item("просрочено на 3 дня — до 01.08", line: 4),
            item("просрочено на 11 дней — до 24.07", line: 5),
        ]
        let top = PrepView.mostUrgent(items, limit: 3, now: now).map(\.lineIndex)
        XCTAssertEqual(top, [5, 4, 3], "самое давнее просроченное — первым, потом неделя")
    }
}
#endif
