import Foundation

#if os(macOS)

/// Режим группировки экрана задач. Хранится между запусками: выбранный
/// взгляд — привычка, а не разовый фильтр (тот же принцип, что у тумблеров
/// контуров в SuflerService).
enum TasksGrouping: String, CaseIterable {
    case byMeeting
    case byDue
}

/// Чистая политика экрана задач (макет docs/design/MOBILE_2026-08.md,
/// раздел «macOS: доделки»): сводка трёх чисел и корзины сроков —
/// тестируются без UI и без TasksService.
enum TasksScreenPolicy {

    struct Summary: Equatable {
        let overdue: Int
        let open: Int      // открытые БЕЗ просроченных: три числа не пересекаются
        let done: Int
    }

    /// «2 просрочено · 9 открыто · 3 сделано» — вместо голого счётчика.
    static func summary(_ items: [(text: String, done: Bool)],
                        now: Date = Date(),
                        calendar: Calendar = .current) -> Summary {
        var overdue = 0, open = 0, done = 0
        for item in items {
            if item.done { done += 1; continue }
            if case .overdue = TaskDue.parse(item.text)?
                .status(now: now, calendar: calendar) {
                overdue += 1
            } else {
                open += 1
            }
        }
        return Summary(overdue: overdue, open: open, done: done)
    }

    /// Корзины режима «По сроку». Порядок — это и порядок секций на экране:
    /// горящее сверху, бессрочное ниже, «Сделанные» — в самом низу, и секция
    /// подчиняется тумблеру «Сделанные», как и в режиме по встречам: скрыто —
    /// значит скрыто везде (круг по PR #367, DeepSeek — прежний комментарий
    /// обещал «на месте», чего код осознанно не делает).
    enum DueBucket: Int, CaseIterable, Comparable {
        case overdue
        case week
        case later
        case undated
        case done

        static func < (a: DueBucket, b: DueBucket) -> Bool { a.rawValue < b.rawValue }

    }

    static func bucket(text: String, done: Bool,
                       now: Date = Date(),
                       calendar: Calendar = .current) -> DueBucket {
        if done { return .done }
        guard let due = TaskDue.parse(text) else { return .undated }
        switch due.status(now: now, calendar: calendar) {
        case .overdue: return .overdue
        case .soon: return .week
        case .later: return .later
        }
    }
}

#endif
