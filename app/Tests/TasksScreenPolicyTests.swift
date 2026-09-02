import XCTest
@testable import CharoiteApp

/// Экран задач по макету MOBILE_2026-08: сводка и корзины сроков.
final class TasksScreenPolicyTests: XCTestCase {

    // 20 августа: «до 13.08» просрочено, «до 25.08» — ближайшая неделя.
    private var now: Date {
        var c = DateComponents(); c.year = 2026; c.month = 8; c.day = 20
        return Calendar.current.date(from: c)!
    }

    /// Сдвиг от `now` в днях и его «дд.мм» — тесты не должны зависеть от
    /// календаря запуска (DS r1 по #479: «до 05.09» с живым Date() падал
    /// бы с 13 сентября, форматтер от Date() — 1–9 января).
    private func day(_ offset: Int) -> Date {
        Calendar.current.date(byAdding: .day, value: offset, to: now)!
    }
    private func ddmm(_ date: Date) -> String {
        let c = Calendar.current.dateComponents([.day, .month], from: date)
        return String(format: "%02d.%02d", c.day!, c.month!)
    }

    func testSummarySplitsOverdueOpenDone() {
        let s = TasksScreenPolicy.summary([
            ("Прислать список — до 15.08", false, day(-10), day(-10)),   // просрочено 5 дней — на виду
            ("Согласовать доступ — до 25.08", false, day(-1), day(-1)), // открыто (скоро)
            ("Собрать статистику", false, day(-1), day(-1)),            // открыто (без срока)
            ("Завести ветку", true, day(-1), day(-1)),                  // сделано
            ("**Коля** — отчёт до 01.08", false, day(-30), day(-30)),   // старое: просрочено 19 дней
            ("**Коля** — без срока", false, day(-30), nil),             // старое: 30 дней, заметка без якоря
        ], owner: "Марк", now: now)
        XCTAssertEqual(s, .init(overdue: 1, open: 2, stale: 2, done: 1),
                       "четыре числа обязаны не пересекаться: старое не «просрочено»")
    }

    func testBucketsFollowDueStatusAndDoneGoesLast() {
        func b(_ text: String, done: Bool = false,
               at anchor: Date? = nil) -> TasksScreenPolicy.DueBucket {
            TasksScreenPolicy.bucket(text: text, done: done, anchor: anchor, now: now)
        }
        XCTAssertEqual(b("отчёт — до 13.08"), .overdue)
        XCTAssertEqual(b("созвон — до 25.08"), .week)
        XCTAssertEqual(b("ревизия — до 15.01"), .later,
                       "январь из августа — следующий год, не просрочка")
        XCTAssertEqual(b("план — до 15.03", at: now), .later,
                       "с якорем: март, названный в августе, — будущий, не 158 дней просрочки")
        XCTAssertEqual(b("план — до 15.03", at: day(-180)), .overdue,
                       "с якорем: март, названный в феврале, — просрочен")
        XCTAssertEqual(b("долг — до 15.08", at: now), .overdue,
                       "срок, пропущенный на днях и принесённый на встречу, — просрочка, не будущий год")
        XCTAssertEqual(b("план — до 01.08", at: now), .later,
                       "а за пределами люфта (19 дней до встречи) — уже следующий год")
        XCTAssertEqual(b("без даты вовсе"), .undated)
        XCTAssertEqual(b("сделано — до 13.08", done: true), .done,
                       "сделанному сроку нечего требовать — корзина одна")
        // порядок секций на экране = порядок кейсов: горящее сверху
        XCTAssertLessThan(TasksScreenPolicy.DueBucket.overdue,
                          TasksScreenPolicy.DueBucket.done)
    }
}

extension TasksScreenPolicyTests {
    /// №152/запрос 01.09: «за мной — первым, даже если задачи далеко».
    func testMineDetection() {
        XCTAssertTrue(TasksScreenPolicy.isMine("**Марк** — зайти в отдел", owner: "Марк"))
        XCTAssertTrue(TasksScreenPolicy.isMine("**Марк + Коля** — свериться", owner: "Марк"))
        XCTAssertTrue(TasksScreenPolicy.isMine("**марк** — письмо", owner: "Марк"))
        XCTAssertFalse(TasksScreenPolicy.isMine("**Света** — рассчитать дельты", owner: "Марк"))
        XCTAssertFalse(TasksScreenPolicy.isMine("**Все участники** — заводить фичи", owner: "Марк"))
        XCTAssertFalse(TasksScreenPolicy.isMine("починить Марку доступ", owner: "Марк"),
                       "имя не в позиции ответственного — не моё")
        XCTAssertFalse(TasksScreenPolicy.isMine("**Марк** — что-то", owner: ""),
                       "пустой user_name ничего не присваивает")
        // Границы круга 1 (DS r1 по #475):
        XCTAssertFalse(TasksScreenPolicy.isMine("**Маркус** — сверить дельты", owner: "Марк"),
                       "подстрока чужого имени — не моё")
        XCTAssertFalse(TasksScreenPolicy.isMine("связаться с Марком — до пятницы", owner: "Марк"),
                       "без ведущего болда ответственного нет")
        XCTAssertTrue(TasksScreenPolicy.isMine("**Марк** — дело", owner: "Марк Ковалёв"),
                      "полное имя в конфиге против короткого в минутках")
        XCTAssertTrue(TasksScreenPolicy.isMine("**Марк Ковалёв** — дело", owner: "Марк"))
        XCTAssertTrue(TasksScreenPolicy.isMine("**Ковалёв** — дело", owner: "Марк Ковалёв"),
                      "фамилия из user_name — тоже владелец (канон speaker_names)")
    }

    func testSplitCutoffBoundary() {
        // ровно 14 дней — ещё не старьё (строгое <), день в день живёт
        let edge = day(-TasksScreenPolicy.staleAfterDays)
        let s = TasksScreenPolicy.split([("**Коля** — ровная граница", edge, edge)],
                                        owner: "Марк", now: now)
        XCTAssertEqual(s.fresh, [0], "день в день — ещё живое")
    }

    func testOverdueWeekGoesStale() {
        // Уточнение владельца (ночь 01.09): срок, просроченный на неделю и
        // больше, — тоже «Старые»; свежая просрочка остаётся на виду.
        let meeting = day(-20)
        let items: [(text: String, happenedAt: Date, anchor: Date?)] = [
            ("**Коля** — отчёт до \(ddmm(day(-9)))", meeting, meeting),
            ("**Коля** — письмо до \(ddmm(day(-3)))", meeting, meeting),
            ("**Марк** — моё до \(ddmm(day(-9)))", meeting, meeting),
            ("**Коля** — ровно неделя, до \(ddmm(day(-7)))", meeting, meeting),
            ("**Коля** — шесть дней, до \(ddmm(day(-6)))", meeting, meeting),
            // якорь года: срок, названный на встрече, не раньше её самой —
            // без якоря «до 15.03» из августа читалось бы как 158 дней
            // просрочки и сворачивалось в день постановки
            ("**Коля** — план до 15.03", day(-1), day(-1)),
            // а февральское «до 20.02» в августе — просрочка, не будущий год
            ("**Коля** — отчёт до 20.02", day(-200), day(-200)),
            // заметка без даты в имени: якоря нет, mtime им не становится —
            // «до 15.03» в файле, тронутом вчера, остаётся просрочкой по
            // прежней догадке (158 дней) и уходит в «Старые»
            ("**Коля** — план до 15.03 (заметка)", day(-1), nil),
        ]
        let s = TasksScreenPolicy.split(items, owner: "Марк", now: now)
        XCTAssertEqual(s.stale, [0, 3, 6, 7], "9 и 7 дней просрочки, февральский срок, заметка — вниз")
        XCTAssertEqual(s.fresh, [1, 4, 5], "3 и 6 дней — на виду; будущий март — живой")
        XCTAssertEqual(s.mine, [2], "моё побеждает и недельную просрочку")
    }

    func testSplitMineFirstEvenWhenOld() {
        let old = day(-40)
        let fresh = day(0).addingTimeInterval(-3600)
        let items: [(text: String, happenedAt: Date, anchor: Date?)] = [
            ("**Света** — свежая", fresh, fresh),
            ("**Марк** — давняя, но моя", old, old),
            ("**Коля** — старьё без срока", old, old),
            ("**Коля** — старая, но со сроком до \(ddmm(day(2)))", old, old),
        ]
        let s = TasksScreenPolicy.split(items, owner: "Марк", now: now)
        XCTAssertEqual(s.mine, [1], "моё не тонет в старых")
        XCTAssertEqual(s.stale, [2], "старое без срока — в «Старые»")
        XCTAssertEqual(s.fresh, [0, 3], "срок держит задачу живой")
    }
}
