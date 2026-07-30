import Foundation

/// Решение «предложить начать запись или промолчать». Сигнал, не действие.
///
/// Забытая кнопка «Запись» — единственная ошибка в этом продукте, которую
/// нельзя исправить потом: без звука не будет ни стенограммы, ни минуток, ни
/// узлов графа. При этом приложение уже знает, что встреча идёт, — календарь
/// читается (opt-in, только чтение) ради брифа по архиву.
///
/// Чего здесь сознательно нет: включения записи самостоятельно. Запись
/// разговора без ведома человека за машиной — то, за что судятся с облачными
/// сервисами; Чароит спрашивает и ждёт ответа. Отказ («не сейчас») запоминается
/// на эту встречу, чтобы подсказка не превратилась в навязчивость.
///
/// Логика намеренно чистая: ни календаря, ни таймеров, ни интерфейса — только
/// вход и решение. Поэтому она проверяется тестами без запуска приложения.
enum MeetingCue {
    /// Встреча календаря в том виде, в каком её видит решение.
    struct Event: Identifiable, Equatable {
        let id: String
        let title: String
        let start: Date
        let end: Date
        /// Сколько людей, кроме владельца календаря. Ноль — это напоминание
        /// («забрать посылку»), а не встреча: записывать нечего.
        let attendees: Int
        let isAllDay: Bool
    }

    /// Готовая подсказка: что показать и на какую встречу она ссылается.
    struct Cue: Equatable {
        let id: String
        let title: String
        let prompt: String
    }

    /// За сколько до начала уже осмысленно предлагать: люди заходят в звонок
    /// заранее, и подсказка «за минуту» приходит вовремя.
    static let leadIn: TimeInterval = 2 * 60
    /// Сколько после начала подсказка ещё уместна. Через десять минут
    /// разговора предлагать запись поздно: половина уже потеряна, а всплывшее
    /// окно посреди встречи мешает больше, чем помогает.
    static let graceAfterStart: TimeInterval = 10 * 60

    /// Предложить запись — или nil, если предлагать не надо.
    ///
    /// - Parameters:
    ///   - events: события календаря (уже прочитанные, любой порядок).
    ///   - isRecording: идёт ли запись прямо сейчас.
    ///   - silencedIds: встречи, по которым человек сказал «не сейчас».
    static func decide(now: Date, events: [Event], isRecording: Bool,
                       silencedIds: Set<String>) -> Cue? {
        guard !isRecording else { return nil }
        let candidates = events.filter { ev in
            guard !ev.isAllDay, ev.attendees > 0, !silencedIds.contains(ev.id) else { return false }
            let since = now.timeIntervalSince(ev.start)
            return since >= -leadIn && since <= graceAfterStart && now < ev.end
        }
        // Из двух подходящих берём начавшуюся ближе к «сейчас»: это та, в
        // которой человек, скорее всего, и сидит.
        guard let ev = candidates.min(by: {
            abs(now.timeIntervalSince($0.start)) < abs(now.timeIntervalSince($1.start))
        }) else { return nil }
        return Cue(id: ev.id, title: ev.title, prompt: prompt(for: ev.title))
    }

    /// Текст подсказки. Вопрос, а не утверждение: решение остаётся за человеком.
    static func prompt(for title: String) -> String {
        L.t("Встреча «\(title)» началась — начать запись?",
            "«\(title)» has started — start recording?",
            "会议「\(title)」已开始——开始录制吗？")
    }
}
