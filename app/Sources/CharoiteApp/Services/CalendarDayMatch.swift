import Foundation

/// Сопоставление событий календаря с записями архива для экрана «Календарь».
///
/// Правило одно: запись принадлежит событию, если она началась в его окне —
/// от четверти часа до начала (пришли заранее и включили запись) и до конца
/// события. Из нескольких подходящих событий побеждает ближайшее по началу,
/// чтобы две встречи подряд не делили одну запись. Логика отделена от
/// EventKit и вью ради тестов: даты сюда приходят готовыми.
enum CalendarDayMatch {
    enum EmptyState: Equatable {
        case none
        case calendarUnavailable
        case quietDay
    }

    /// Пустая лента не всегда значит пустой календарь: без разрешения мы
    /// знаем только об отсутствии записей и не имеем права утверждать, что
    /// событий не было.
    static func emptyState(recordCount: Int, eventCount: Int,
                           calendarConnected: Bool) -> EmptyState {
        guard recordCount == 0, eventCount == 0 else { return .none }
        return calendarConnected ? .quietDay : .calendarUnavailable
    }

    /// Событие дня и записи, которые к нему прикрепились.
    struct Slot: Identifiable, Equatable {
        let event: CalendarService.DayEvent
        var recordIDs: [String] = []
        var id: String { event.id }
    }

    /// Раскладка дня: события со своими записями и записи-сироты.
    ///
    /// Запись без события-пары — отдельной лентой, а не потерей: разговор в
    /// коридоре тоже встреча, даже если календарь о нём молчал.
    struct DayBoard: Equatable {
        var slots: [Slot] = []
        var looseRecordIDs: [String] = []
    }

    /// Запись начинается около начала события — конец записи не нужен,
    /// поэтому короткое событие без даты конца получает окно в полчаса.
    static func board(events: [CalendarService.DayEvent],
                      recordStarts: [(id: String, start: Date)]) -> DayBoard {
        var slots = events.map { Slot(event: $0) }
        var loose: [String] = []
        for record in recordStarts {
            var bestIndex: Int?
            var bestDistance = TimeInterval.greatestFiniteMagnitude
            for (index, slot) in slots.enumerated() {
                let start = slot.event.start
                let end = slot.event.end ?? start.addingTimeInterval(30 * 60)
                let windowStart = start.addingTimeInterval(-15 * 60)
                let windowEnd = max(end, start.addingTimeInterval(30 * 60))
                guard record.start >= windowStart, record.start <= windowEnd else { continue }
                let distance = abs(record.start.timeIntervalSince(start))
                if distance < bestDistance {
                    bestDistance = distance
                    bestIndex = index
                }
            }
            if let bestIndex {
                slots[bestIndex].recordIDs.append(record.id)
            } else {
                loose.append(record.id)
            }
        }
        return DayBoard(slots: slots, looseRecordIDs: loose)
    }
}
