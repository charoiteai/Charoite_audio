import EventKit
import Foundation

/// Ближайшая встреча из календаря — для брифа по одному клику.
///
/// Строго opt-in (тумблер в Настройках) и read-only: приложение читает
/// только название и время ближайшего события, чтобы предложить бриф по
/// архиву. Ничего не пишет, никуда не отправляет — EventKit локален.
@MainActor
final class CalendarService: ObservableObject {
    static let shared = CalendarService()

    @Published private(set) var nextEventTitle: String?
    /// Подсказка «встреча началась — начать запись?». Решение принимает
    /// MeetingCue, здесь только события и ответ пользователя.
    @Published private(set) var cue: MeetingCue.Cue?
    private let store = EKEventStore()
    private var timer: Timer?
    private var silenced: Set<String> = []
    /// Идёт ли запись — знает демон; сюда это состояние приносит вид.
    private var isRecording = false

    /// Запросить доступ (системный диалог) и начать следить за ближайшим
    /// событием. Отказ пользователя — тихо выключаемся.
    /// - Parameter askForNotifications: спрашивать ли разрешение на баннеры.
    ///   Только когда человек сам щёлкнул тумблер. При автозапуске приложение
    ///   поднимается в фоне без единого действия пользователя, и системный
    ///   диалог, выскочивший сам по себе после входа в систему, выглядит как
    ///   навязчивость — а отказ в нём закрывает функцию навсегда.
    func enable(askForNotifications: Bool = false) {
        if askForNotifications {
            MeetingNotificationService.shared.requestAuthorization()
        }
        let done: (Bool) -> Void = { granted in
            Task { @MainActor in
                guard granted else { self.nextEventTitle = nil; return }
                self.refresh()
                self.timer?.invalidate()
                // Минута, а не пять: окно подсказки о начале встречи —
                // от двух минут до начала до десяти после, и на пятиминутном
                // такте половина этого окна проходила молча.
                self.timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
                    Task { @MainActor [weak self] in self?.refresh() }
                }
            }
        }
        if #available(macOS 14.0, *) {
            store.requestFullAccessToEvents { granted, _ in done(granted) }
        } else {
            store.requestAccess(to: .event) { granted, _ in done(granted) }
        }
    }

    func disable() {
        timer?.invalidate()
        timer = nil
        nextEventTitle = nil
        if let id = cue?.id { MeetingNotificationService.shared.remove(cueID: id) }
        cue = nil
        MeetingNotificationService.shared.reset()
    }

    /// Состояние записи из вида: при идущей записи подсказка молчит.
    func recording(_ on: Bool) {
        guard isRecording != on else { return }
        isRecording = on
        refresh()
    }

    /// «Не сейчас» по этой встрече: больше не спрашиваем про неё.
    func dismissCue() {
        if let id = cue?.id {
            silenced.insert(id)
            MeetingNotificationService.shared.remove(cueID: id)
        }
        cue = nil
    }

    /// Событие в окне «идёт сейчас или начнётся в ближайший час».
    private func refresh() {
        let now = Date()
        let predicate = store.predicateForEvents(
            withStart: now.addingTimeInterval(-5 * 60),
            end: now.addingTimeInterval(60 * 60),
            calendars: nil)
        let raw = store.events(matching: predicate)
            .filter { !$0.isAllDay && !($0.title ?? "").isEmpty }
            .sorted { $0.startDate < $1.startDate }
        nextEventTitle = raw.first?.title
        // Число участников кроме владельца: событие без людей — напоминание,
        // а не встреча. EventKit отдаёт attendees только когда приглашение
        // пришло из календарной системы, поэтому одиночная встреча в звонке
        // без приглашения подсказки не получит — так честнее, чем предлагать
        // запись на «сходить к врачу».
        let events = raw.map { ev in
            MeetingCue.Event(id: ev.eventIdentifier ?? (ev.title ?? "") + "\(ev.startDate!)",
                             title: ev.title ?? "",
                             start: ev.startDate,
                             end: ev.endDate,
                             attendees: max(0, (ev.attendees?.count ?? 0) - 1),
                             isAllDay: ev.isAllDay)
        }
        let nextCue = MeetingCue.decide(now: now, events: events,
                                        isRecording: isRecording, silencedIds: silenced)
        if let oldID = cue?.id, oldID != nextCue?.id {
            MeetingNotificationService.shared.remove(cueID: oldID)
        }
        cue = nextCue
        if let nextCue {
            MeetingNotificationService.shared.present(nextCue)
        }
    }
}
