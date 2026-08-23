import Foundation

#if os(macOS)

/// Чистая политика библиотеки встреч (макет docs/design/MOBILE_2026-08.md,
/// раздел «macOS: доделки», экран 3): группы по дням, сводка состояний и
/// подписи чисел — тестируются без UI и без репозитория, как
/// `TasksScreenPolicy` у экрана задач.
enum LibraryScreenPolicy {

    /// Порядок — это и порядок секций ленты: свежее сверху.
    enum Bucket: Int, CaseIterable, Comparable {
        case today
        case week
        case earlier

        static func < (a: Bucket, b: Bucket) -> Bool { a.rawValue < b.rawValue }

        var title: String {
            switch self {
            case .today: return L.t("Сегодня", "Today", "今天")
            case .week: return L.t("На этой неделе", "This week", "本周")
            case .earlier: return L.t("Раньше", "Earlier", "更早")
            }
        }
    }

    /// «Сегодня» — тот же календарный день; «На этой неделе» — та же
    /// календарная неделя, что у `now` (как полоса недели над лентой);
    /// остальное — «Раньше». Запись из будущего (часы разъехались) — не
    /// «раньше»: кладём в «Сегодня», иначе свежая встреча уезжает вниз.
    static func bucket(of date: Date, now: Date = Date(),
                       calendar: Calendar = .current) -> Bucket {
        if calendar.isDate(date, inSameDayAs: now) || date > now { return .today }
        if let week = calendar.dateInterval(of: .weekOfYear, for: now), week.contains(date) {
            return .week
        }
        return .earlier
    }

    /// Секции ленты: пустые корзины не показываем, внутри — новое первым.
    static func sections<T>(_ items: [T], date: (T) -> Date,
                            now: Date = Date(),
                            calendar: Calendar = .current) -> [(bucket: Bucket, items: [T])] {
        var byBucket: [Bucket: [T]] = [:]
        for item in items {
            byBucket[bucket(of: date(item), now: now, calendar: calendar), default: []].append(item)
        }
        return Bucket.allCases.compactMap { bucket in
            guard let group = byBucket[bucket], !group.isEmpty else { return nil }
            return (bucket, group.sorted { date($0) > date($1) })
        }
    }

    struct Summary: Equatable {
        let total: Int
        let processing: Int
        let failed: Int
    }

    /// «8 встреч · 1 собирается · 1 с ошибкой» — у каждого числа источник:
    /// состояние конвейера, а не самочувствие интерфейса.
    static func summary(_ states: [MeetingProcessingSnapshot.State]) -> Summary {
        Summary(total: states.count,
                processing: states.filter { $0 == .processing }.count,
                failed: states.filter { $0 == .error }.count)
    }

    /// Русское склонение по числу: 1 участник, 2 участника, 5 участников;
    /// английское — по одному признаку; китайское не склоняется.
    static func plural(_ n: Int, ru: (one: String, few: String, many: String),
                       en: (one: String, many: String), zh: String) -> String {
        switch L.lang {
        case "en": return "\(n) " + (n == 1 ? en.one : en.many)
        case "zh": return "\(n) " + zh
        default:
            let mod10 = n % 10, mod100 = n % 100
            let form: String
            if mod10 == 1 && mod100 != 11 {
                form = ru.one
            } else if (2...4).contains(mod10) && !(12...14).contains(mod100) {
                form = ru.few
            } else {
                form = ru.many
            }
            return "\(n) " + form
        }
    }

    static func meetings(_ n: Int) -> String {
        plural(n, ru: ("встреча", "встречи", "встреч"), en: ("meeting", "meetings"), zh: "场会议")
    }

    static func participants(_ n: Int) -> String {
        plural(n, ru: ("участник", "участника", "участников"),
               en: ("participant", "participants"), zh: "位参会者")
    }

    static func tasks(_ n: Int) -> String {
        plural(n, ru: ("поручение", "поручения", "поручений"),
               en: ("action item", "action items"), zh: "项任务")
    }

    /// Строка под заголовком карточки: длительность, участники, поручения —
    /// только то, что у встречи есть. Нули и неизвестное не пишем: «0 поручений»
    /// читается как упрёк, а не как факт.
    static func meta(duration: String?, participants: Int, tasks: Int) -> [String] {
        var out: [String] = []
        if let duration, !duration.isEmpty { out.append(duration) }
        if participants > 0 { out.append(Self.participants(participants)) }
        if tasks > 0 { out.append(Self.tasks(tasks)) }
        return out
    }
}

#endif
