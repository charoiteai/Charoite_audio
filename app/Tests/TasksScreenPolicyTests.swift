import XCTest
@testable import CharoiteApp

/// Экран задач по макету MOBILE_2026-08: сводка и корзины сроков.
final class TasksScreenPolicyTests: XCTestCase {

    // 20 августа: «до 13.08» просрочено, «до 25.08» — ближайшая неделя.
    private var now: Date {
        var c = DateComponents(); c.year = 2026; c.month = 8; c.day = 20
        return Calendar.current.date(from: c)!
    }

    func testSummarySplitsOverdueOpenDone() {
        let s = TasksScreenPolicy.summary([
            ("Прислать список — до 13.08", false),   // просрочено
            ("Согласовать доступ — до 25.08", false), // открыто (скоро)
            ("Собрать статистику", false),            // открыто (без срока)
            ("Завести ветку", true),                  // сделано
        ], now: now)
        XCTAssertEqual(s, .init(overdue: 1, open: 2, done: 1),
                       "три числа обязаны не пересекаться")
    }

    func testBucketsFollowDueStatusAndDoneGoesLast() {
        func b(_ text: String, done: Bool = false) -> TasksScreenPolicy.DueBucket {
            TasksScreenPolicy.bucket(text: text, done: done, now: now)
        }
        XCTAssertEqual(b("отчёт — до 13.08"), .overdue)
        XCTAssertEqual(b("созвон — до 25.08"), .week)
        XCTAssertEqual(b("ревизия — до 15.01"), .later,
                       "январь из августа — следующий год, не просрочка")
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
        XCTAssertTrue(TasksScreenPolicy.isMine("**Антон** — зайти в ДАДМ", owner: "Антон"))
        XCTAssertTrue(TasksScreenPolicy.isMine("**Антон + Коля** — свериться", owner: "Антон"))
        XCTAssertTrue(TasksScreenPolicy.isMine("**антон** — письмо", owner: "Антон"))
        XCTAssertFalse(TasksScreenPolicy.isMine("**Света** — рассчитать дельты", owner: "Антон"))
        XCTAssertFalse(TasksScreenPolicy.isMine("**Все участники** — заводить фичи", owner: "Антон"))
        XCTAssertFalse(TasksScreenPolicy.isMine("починить Антону доступ", owner: "Антон"),
                       "имя не в позиции ответственного — не моё")
        XCTAssertFalse(TasksScreenPolicy.isMine("**Антон** — что-то", owner: ""),
                       "пустой user_name ничего не присваивает")
        // Границы круга 1 (DS r1 по #475):
        XCTAssertFalse(TasksScreenPolicy.isMine("**Антонина** — сверить дельты", owner: "Антон"),
                       "подстрока чужого имени — не моё")
        XCTAssertFalse(TasksScreenPolicy.isMine("связаться с Антоном — до пятницы", owner: "Антон"),
                       "без ведущего болда ответственного нет")
        XCTAssertTrue(TasksScreenPolicy.isMine("**Антон** — дело", owner: "Антон Кузьменков"),
                      "полное имя в конфиге против короткого в минутках")
        XCTAssertTrue(TasksScreenPolicy.isMine("**Антон Кузьменков** — дело", owner: "Антон"))
        XCTAssertTrue(TasksScreenPolicy.isMine("**Кузьменков** — дело", owner: "Антон Кузьменков"),
                      "фамилия из user_name — тоже владелец (канон speaker_names)")
    }

    func testSplitCutoffBoundary() {
        // ровно 14 дней — ещё не старьё (строгое <), день в день живёт
        let cal = Calendar.current
        let now = Date()
        let edge = cal.date(byAdding: .day, value: -TasksScreenPolicy.staleAfterDays, to: now)!
        let s = TasksScreenPolicy.split([("**Коля** — ровная граница", edge)],
                                        owner: "Антон", now: now, calendar: cal)
        XCTAssertEqual(s.fresh, [0], "день в день — ещё живое")
    }

    func testOverdueWeekGoesStale() {
        // Уточнение владельца (ночь 01.09): срок, просроченный больше
        // недели, — тоже «Старые»; свежая просрочка остаётся на виду.
        let cal = Calendar.current
        let now = Date()
        let f = DateFormatter(); f.dateFormat = "dd.MM"
        let d9 = f.string(from: cal.date(byAdding: .day, value: -9, to: now)!)
        let d3 = f.string(from: cal.date(byAdding: .day, value: -3, to: now)!)
        let recent = cal.date(byAdding: .day, value: -2, to: now)!
        let items: [(text: String, happenedAt: Date)] = [
            ("**Коля** — отчёт до \(d9)", recent),
            ("**Коля** — письмо до \(d3)", recent),
            ("**Антон** — моё до \(d9)", recent),
        ]
        let s = TasksScreenPolicy.split(items, owner: "Антон", now: now, calendar: cal)
        XCTAssertEqual(s.stale, [0], "просрочка 9 дней — вниз")
        XCTAssertEqual(s.fresh, [1], "просрочка 3 дня — на виду в «Просрочено»")
        XCTAssertEqual(s.mine, [2], "моё побеждает и недельную просрочку")
    }

    func testSplitMineFirstEvenWhenOld() {
        let old = Date(timeIntervalSinceNow: -40 * 86_400)
        let fresh = Date(timeIntervalSinceNow: -3600)
        let items: [(text: String, happenedAt: Date)] = [
            ("**Света** — свежая", fresh),
            ("**Антон** — давняя, но моя", old),
            ("**Коля** — старьё без срока", old),
            ("**Коля** — старая, но со сроком до 05.09", old),
        ]
        let s = TasksScreenPolicy.split(items, owner: "Антон")
        XCTAssertEqual(s.mine, [1], "моё не тонет в старых")
        XCTAssertEqual(s.stale, [2], "старое без срока — в «Старые»")
        XCTAssertEqual(s.fresh, [0, 3], "срок держит задачу живой")
    }
}
