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

    private func item(_ text: String, line: Int, on day: Date? = nil) -> TasksService.Item {
        TasksService.Item(id: "f#\(line)", file: URL(fileURLWithPath: "/tmp/f.md"), rel: "f.md",
                          lineIndex: line, text: text, done: false,
                          fileDate: day ?? date(2026, 8, 1), sourceLine: "- [ ] \(text)")
    }

    func testSummaryNamesEveryBucketAndSkipsZeros() {
        let now = date(2026, 8, 4)
        let summary = PrepView.debtsSummary(
            ["отчёт до 24.07", "созвон к 06.08", "собрать цифры без даты"].map { ($0, nil) },
            now: now)
        XCTAssertEqual(summary, "1 просрочено · 1 на этой неделе · 1 без срока")
        XCTAssertEqual(PrepView.debtsSummary([("без срока", nil)], now: now), "1 без срока",
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
    // №755: «Что вы обещали» — те же «Мои», что на экране задач, а не все открытые.
    func testPromisedKeepsOnlyTheOwnersItemsEvenOldOnes() {
        let items = [
            item("**Света** — рассчитать дельты", line: 1),
            item("**Марк** — зайти в отдел", line: 2, on: date(2026, 5, 1)),
            item("позвонить в банк", line: 3),
            item("**Марк Ковалёв** — письмо до 01.08", line: 4),
        ]
        let picked = PrepView.promised(items, owner: "Марк").map(\.lineIndex)
        XCTAssertEqual(picked, [2, 4], "чужие и без исполнителя не входят; своё давнее остаётся — свои не стареют")
    }

    func testPromisedFallsBackToBaseWhenOwnerIsUnknown() {
        let items = [item("**Света** — дельты", line: 1), item("позвонить в банк", line: 2)]
        XCTAssertEqual(PrepView.promised(items, owner: "").map(\.lineIndex), [1, 2],
                       "имя не задано — список как раньше, блок не пустеет")
        XCTAssertEqual(PrepView.promised(items, owner: "Марк").map(\.lineIndex), [1, 2],
                       "своих не нашлось — список как раньше")
    }

    func testSummaryAndTopCountTheSameOwnedSet() {
        let now = date(2026, 8, 4)
        let items = [
            item("**Света** — отчёт до 24.07", line: 1),
            item("**Марк** — собрать цифры", line: 2),
        ]
        let open = PrepView.promised(items, owner: "Марк")
        XCTAssertEqual(PrepView.debtsSummary(open.map { ($0.text, $0.dueAnchor) }, now: now), "1 без срока",
                       "чужая просрочка не попадает в сводку «Что вы обещали»")
        XCTAssertEqual(PrepView.mostUrgent(open, limit: 3, now: now).map(\.lineIndex), [2])
    }

    func testMostUrgentPrefersFresherMeetingsInsideOneBucket() {
        let now = date(2026, 8, 20)
        let items = (1...5).map { i in item("**Марк** — дело \(i)", line: i, on: date(2026, 8, i)) }
        XCTAssertEqual(PrepView.mostUrgent(items, limit: 3, now: now).map(\.lineIndex), [5, 4, 3],
                       "без срока — свежие встречи первыми, а не порядок файла")
    }
}
#endif
