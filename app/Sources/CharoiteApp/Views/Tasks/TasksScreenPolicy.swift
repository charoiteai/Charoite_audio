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

    /// «Старые» (свёрнутая секция внизу; файлы не трогаем — чистится
    /// экран, а не история). Правило владельца (01.09, уточнено ночью):
    /// поручение БЕЗ срока — старше 14 дней от встречи; поручение СО
    /// сроком — просрочено больше недели (свежая просрочка остаётся в
    /// «Просрочено», хлам недельной давности уезжает вниз).
    static let staleAfterDays = 14
    static let staleOverdueDays = 7

    /// Поручение «за владельцем»: минутки пишут `**Имя** — дело`, и ТОЛЬКО
    /// ведущий жирный блок — ответственный (fallback по « — » ловил
    /// «связаться с Антоном — до пятницы», DS r1 по #475). Матч по ЦЕЛОМУ
    /// слову: «Антонина» — не «Антон» (DS r1). Owner — первый токен
    /// user_name: в конфиге может лежать полное имя, а минутки пишут
    /// короткое. Пустое имя — секции «Мои» нет, ничего не угадываем.
    static func isMine(_ text: String, owner: String) -> Bool {
        // Любое СЛОВО user_name (имя ИЛИ фамилия, ≥3 букв) — как в
        // python-каноне src/speaker_names.py: конфиг хранит «Имя Фамилия»,
        // минутки пишут одно слово (GLM r1 по #475).
        let words = owner.split(whereSeparator: \.isWhitespace)
            .map(String.init).filter { $0.count >= 3 }
        guard !words.isEmpty else { return false }
        guard let m = text.range(of: #"\*\*[^*]+\*\*"#, options: .regularExpression),
              m.lowerBound == text.startIndex || text[..<m.lowerBound]
                  .trimmingCharacters(in: .whitespaces).isEmpty else { return false }
        let assignee = text[m]
        for word in words {
            guard let r = assignee.range(of: word, options: [.caseInsensitive]) else {
                continue
            }
            let before = assignee[..<r.lowerBound].last
            let after = assignee[r.upperBound...].first
            if (before.map { !$0.isLetter } ?? true)
                && (after.map { !$0.isLetter } ?? true) { return true }
        }
        return false
    }

    /// Открытые поручения одной операцией: «Мои» (первая секция всегда,
    /// даже давние — запрос владельца 01.09), живые и «Старые».
    static func split(_ items: [(text: String, happenedAt: Date)],
                      owner: String,
                      now: Date = Date(),
                      calendar: Calendar = .current)
        -> (mine: [Int], fresh: [Int], stale: [Int]) {
        var mine: [Int] = [], fresh: [Int] = [], stale: [Int] = []
        let cutoff = calendar.date(byAdding: .day, value: -staleAfterDays, to: now)
            ?? now.addingTimeInterval(-Double(staleAfterDays) * 86_400)
        for (i, item) in items.enumerated() {
            if isMine(item.text, owner: owner) {
                mine.append(i)
                continue
            }
            if let due = TaskDue.parse(item.text) {
                if case .overdue(let days) = due.status(now: now, calendar: calendar),
                   days > staleOverdueDays {
                    stale.append(i)
                } else {
                    fresh.append(i)
                }
            } else if item.happenedAt < cutoff {
                stale.append(i)
            } else {
                fresh.append(i)
            }
        }
        return (mine, fresh, stale)
    }
}

#endif
