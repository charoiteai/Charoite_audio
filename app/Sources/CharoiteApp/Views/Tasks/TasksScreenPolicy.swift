import Foundation

#if os(macOS)

/// Режим группировки экрана задач. Хранится между запусками: выбранный
/// взгляд — привычка, а не разовый фильтр (тот же принцип, что у тумблеров
/// контуров в SuflerService).
enum TasksGrouping: String, CaseIterable {
    case byMeeting
    case byDue
}

/// Секции «Мои» и «Старые» — поверх обеих группировок (запрос владельца
/// 01.09: «за мной — первым, даже если задачи далеко; старьё — почистить»).
// Здесь, а не во вью: владельца читает и TasksService — бейдж раздела
// считается той же сводкой, что и шапка экрана (№757).
enum TasksMineStale {
    /// Кэш на процесс: configValue читает config.yaml с диска, а owner
    /// дёргался из трёх computed-секций на каждую букву поиска (GLM r1).
    static let owner: String = AppSettings.configValue("user_name") ?? ""
    static let mineTitle = L.t("Мои", "Mine", "我的")
    static func staleTitle(_ n: Int) -> String {
        L.t("Старые (\(n))", "Stale (\(n))", "旧任务（\(n)）")
    }
}

/// Чистая политика экрана задач (макет docs/design/MOBILE_2026-08.md,
/// раздел «macOS: доделки»): сводка трёх чисел и корзины сроков —
/// тестируются без UI и без TasksService.
enum TasksScreenPolicy {
    /// All query words may match task text or its source, in any order.
    static func matchesSearch(text: String, source: String, query: String) -> Bool {
        // «й» — отдельная буква, а не «и» с диакритикой: «краи» не должно
        // находить «край». Свёртка снимает бреве, поэтому «й» прячем на время.
        let shortI = "\u{E000}"
        func normalized(_ value: String) -> String {
            value.replacingOccurrences(of: "й", with: shortI)
                .replacingOccurrences(of: "Й", with: shortI)
                .folding(options: [.caseInsensitive, .diacriticInsensitive],
                         locale: Locale(identifier: "ru_RU"))
                .replacingOccurrences(of: shortI, with: "й")
        }
        let terms = normalized(query).split(whereSeparator: { $0.isWhitespace })
        let content = normalized(text + " " + source)
        return terms.allSatisfy { content.contains($0) }
    }

    static func statusAction(text: String, done: Bool) -> String {
        let action = done
            ? L.t("Снова открыть поручение", "Reopen action item", "重新打开任务")
            : L.t("Отметить выполненным", "Mark completed", "标记完成")
        return action + ": " + text
    }


    struct Summary: Equatable {
        let overdue: Int
        let open: Int      // открытые БЕЗ просроченных и БЕЗ старых: числа не пересекаются
        let stale: Int     // свёрнутые «Старые» — своя цифра, чтобы счёт не терялся
        let done: Int

        /// «Ждут внимания» — число бейджа раздела и кнопки «Задачи · N»: просрочено
        /// плюс открыто, то есть ровно те два числа, что шапка экрана печатает
        /// первыми. Старые сюда не входят — как и в шапке (#479); прежний бейдж
        /// считал все не сделанные и спорил с экраном, куда вёл (№757).
        var attention: Int { overdue + open }

        static let empty = Summary(overdue: 0, open: 0, stale: 0, done: 0)
    }

    /// «2 просрочено · 9 открыто · 3 старых · 3 сделано» — вместо голого
    /// счётчика. Старые вычтены из первых двух: иначе одна просроченная на
    /// месяц задача давала «1 просрочено» при пустой корзине «Просрочено»
    /// (DS r1 по #479).
    static func summary(_ items: [(text: String, done: Bool, happenedAt: Date, anchor: Date?)],
                        owner: String,
                        now: Date = Date(),
                        calendar: Calendar = .current) -> Summary {
        let open = items.filter { !$0.done }
        let s = split(open.map { ($0.text, $0.happenedAt, $0.anchor) },
                      owner: owner, now: now, calendar: calendar)
        let staleSet = Set(s.stale)
        var overdue = 0, live = 0
        for (i, item) in open.enumerated() where !staleSet.contains(i) {
            if case .overdue = TaskDue.parse(item.text)?
                .status(now: now, calendar: calendar, anchor: item.anchor) {
                overdue += 1
            } else {
                live += 1
            }
        }
        return Summary(overdue: overdue, open: live, stale: s.stale.count,
                       done: items.count - open.count)
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
                       anchor: Date?,
                       now: Date = Date(),
                       calendar: Calendar = .current) -> DueBucket {
        if done { return .done }
        guard let due = TaskDue.parse(text) else { return .undated }
        switch due.status(now: now, calendar: calendar, anchor: anchor) {
        case .overdue: return .overdue
        case .soon: return .week
        case .later: return .later
        }
    }

    /// «Старые» (свёрнутая секция внизу; файлы не трогаем — чистится
    /// экран, а не история). Правило владельца (01.09, уточнено ночью):
    /// поручение БЕЗ срока — старше 14 дней от встречи; поручение СО
    /// сроком — просрочено на неделю и больше (свежая просрочка остаётся
    /// в «Просрочено», хлам недельной давности уезжает вниз). Год срока —
    /// от даты встречи (`anchor`), не от «сегодня»: иначе сентябрьское
    /// «до 15.03» сворачивалось в день постановки, а февральское
    /// «до 20.02» не старело никогда (`TaskDue.status(anchor:)`).
    /// `happenedAt` — возраст для 14-дневной границы (для заметок без
    /// даты в имени это mtime, якорем он не годится — `Item.dueAnchor`).
    static let staleAfterDays = 14
    static let staleOverdueDays = 7

    /// Поручение «за владельцем»: минутки пишут `**Имя** — дело`, и ТОЛЬКО
    /// ведущий жирный блок — ответственный (fallback по « — » ловил
    /// «связаться с Марком — до пятницы», DS r1 по #475). Матч по ЦЕЛОМУ
    /// слову: «Маркус» — не «Марк» (DS r1). Owner — первый токен
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
    static func split(_ items: [(text: String, happenedAt: Date, anchor: Date?)],
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
                if case .overdue(let days) = due.status(now: now, calendar: calendar,
                                                        anchor: item.anchor),
                   days >= staleOverdueDays {
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
