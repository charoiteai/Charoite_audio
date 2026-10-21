import XCTest
@testable import CharoiteApp

#if os(macOS)
/// Бейдж «Задачи» и шапка экрана — одно число (№757): бейдж раньше считал все
/// не сделанные, шапка — без «Старых», и на графе из двух старых поручений
/// панель говорила «Задачи 2», а экран — «0 открыто · 2 старых».
@MainActor
final class TasksBadgeTests: XCTestCase {

    private var now: Date {
        var c = DateComponents(); c.year = 2026; c.month = 8; c.day = 20
        return Calendar.current.date(from: c)!
    }

    private func day(_ offset: Int) -> Date {
        Calendar.current.date(byAdding: .day, value: offset, to: now)!
    }

    func testAttentionIsOverdueAndOpenWithoutStaleOrDone() {
        let s = TasksScreenPolicy.summary([
            ("Прислать список — до 15.08", false, day(-10), day(-10)),   // просрочено 5 дней
            ("Собрать статистику", false, day(-1), day(-1)),            // открыто
            ("**Марк** — давнее своё", false, day(-40), day(-40)),      // моё: в старые не уходит
            ("Завести ветку", true, day(-1), day(-1)),                  // сделано
            ("**Коля** — отчёт до 01.08", false, day(-30), day(-30)),   // старое: просрочка 19 дней
            ("**Коля** — без срока", false, day(-30), nil),             // старое: 30 дней
        ], owner: "Марк", now: now)
        XCTAssertEqual(s, .init(overdue: 1, open: 2, stale: 2, done: 1))
        XCTAssertEqual(s.attention, 3, "бейдж = просрочено + открыто шапки; старые и сделанные — нет")
    }

    func testOnlyStaleTasksGiveNoBadge() {
        // Случай обхода 0.98.0: два открытых поручения, оба старые.
        let s = TasksScreenPolicy.summary([
            ("**Коля** — без срока", false, day(-30), nil),
            ("**Оля** — отчёт до 01.08", false, day(-30), day(-30)),
        ], owner: "Марк", now: now)
        XCTAssertEqual(s.open, 0)
        XCTAssertEqual(s.stale, 2)
        XCTAssertEqual(s.attention, 0)
    }

    /// Граница «Старых» — календарный день: в течение одного дня сводка не
    /// меняется, иначе шапка (живое `Date()`) уходила от бейджа (снимок скана)
    /// посреди дня. Встреча 14 дней назад в 12:00 — живая весь сегодняшний день.
    func testStaleBoundaryDoesNotMoveWithinTheDay() {
        let meeting = day(-TasksScreenPolicy.staleAfterDays).addingTimeInterval(12 * 3600)
        let item = [("**Коля** — без срока", false, meeting, Optional(meeting))]
        let morning = TasksScreenPolicy.summary(item, owner: "Марк", now: now.addingTimeInterval(9 * 3600))
        let evening = TasksScreenPolicy.summary(item, owner: "Марк", now: now.addingTimeInterval(16 * 3600))
        XCTAssertEqual(morning, evening)
        XCTAssertEqual(morning.open, 1)
        let tomorrow = TasksScreenPolicy.summary(item, owner: "Марк", now: day(1).addingTimeInterval(60))
        XCTAssertEqual(tomorrow.stale, 1, "назавтра — старое")
    }

    /// Проводка, а не политика: сервис после скана публикует ту же сводку, что
    /// шапка считает по `items`. Старая формула бейджа (все не сделанные)
    /// здесь дала бы 3 вместо 1.
    func testServicePublishesScreenSummaryAfterScan() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("tasks-badge-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd HH-mm"
        let today = Date()
        let old = Calendar.current.date(byAdding: .day, value: -30, to: today)!
        let fresh = Calendar.current.date(byAdding: .day, value: -1, to: today)!
        func put(_ date: Date, _ body: String) throws {
            let dir = root.appendingPathComponent("Встречи-архив/\(f.string(from: date)) — Тема")
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            try body.write(to: dir.appendingPathComponent("Минутки.md"), atomically: true, encoding: .utf8)
        }
        try put(old, "- [ ] Сверить старый реестр\n- [ ] Закрыть старую заявку\n")
        try put(fresh, "- [ ] Отправить свежий отчёт\n- [x] Созвониться\n")

        let service = TasksService()
        service.rescan(root: root)
        for _ in 0..<200 where service.items.isEmpty {
            try await Task.sleep(nanoseconds: 20_000_000)
        }
        XCTAssertEqual(service.items.filter { !$0.done }.count, 3)
        let screen = TasksScreenPolicy.summary(
            service.items.map { ($0.text, $0.done, $0.happenedAt, $0.dueAnchor) },
            owner: TasksMineStale.owner)
        XCTAssertEqual(service.summary, screen)
        XCTAssertEqual(service.attentionCount, 1, "старые не в бейдже — как и в шапке")
    }
}
#endif
