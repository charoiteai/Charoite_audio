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
    /// Меняется после каждого обновления EventKit. Виды произвольного дня
    /// перечитывают свой срез, а не держат снимок до повторного открытия.
    @Published private(set) var eventsRevision = 0
    /// Подсказка «встреча началась — начать запись?». Решение принимает
    /// MeetingCue, здесь только события и ответ пользователя.
    @Published private(set) var cue: MeetingCue.Cue?
    private let store = EKEventStore()
    private var timer: Timer?
    private var silenced: Set<String> = []
    /// Идёт ли запись — знает демон; сюда это состояние приносит вид.
    private var isRecording = false

    private init() {
        NotificationCenter.default.addObserver(
            forName: .EKEventStoreChanged, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, self.accessGranted == true else { return }
                self.refresh()
            }
        }
    }

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
                self.accessGranted = granted
                guard granted else {
                    self.nextEventTitle = nil
                    self.today = []
                    self.eventsRevision &+= 1
                    return
                }
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
        today = []
        // Разрешение macOS может сохраниться, но пользователь выключил
        // opt-in функцию: до нового enable() EventKit для продукта недоступен.
        accessGranted = nil
        eventsRevision &+= 1
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

    /// Оставшиеся встречи сегодняшнего дня — для экрана подготовки.
    struct DayEvent: Identifiable, Equatable {
        let id: String
        let title: String
        let start: Date
        var end: Date?
        let attendees: Int
    }

    @Published private(set) var today: [DayEvent] = []

    /// Дал ли пользователь доступ к календарю. nil — ещё не спрашивали:
    /// экрану «Календарь» нужно отличать «доступа нет» от «день пустой»,
    /// это разные состояния и разные кнопки.
    @Published private(set) var accessGranted: Bool?

    /// События произвольного дня — для экрана «Календарь». Синхронно:
    /// EventKit отвечает из локальной базы. Без доступа — пусто.
    func events(on day: Date) -> [DayEvent] {
        guard accessGranted == true else { return [] }
        let start = Calendar.current.startOfDay(for: day)
        let end = Calendar.current.date(byAdding: .day, value: 1, to: start)
            ?? start.addingTimeInterval(24 * 3600)
        let predicate = store.predicateForEvents(withStart: start, end: end, calendars: nil)
        return store.events(matching: predicate)
            .filter { !$0.isAllDay && !($0.title ?? "").isEmpty }
            .sorted { $0.startDate < $1.startDate }
            .map { ev in
                DayEvent(id: ev.eventIdentifier ?? (ev.title ?? "") + "\(ev.startDate!)",
                         title: ev.title ?? "",
                         start: ev.startDate,
                         end: ev.endDate,
                         attendees: max(0, (ev.attendees?.count ?? 0) - 1))
            }
    }

    /// Событие в окне «идёт сейчас или начнётся в ближайший час».
    private func refresh() {
        guard accessGranted == true else {
            today = []
            nextEventTitle = nil
            cue = nil
            eventsRevision &+= 1
            return
        }
        let now = Date()
        // Отдельным запросом — весь остаток дня: подсказке о записи хватает
        // часа, а подготовке нужен список «что сегодня ещё будет».
        let dayEnd = Calendar.current.startOfDay(for: now).addingTimeInterval(24 * 3600)
        let dayPredicate = store.predicateForEvents(
            withStart: now.addingTimeInterval(-5 * 60), end: dayEnd, calendars: nil)
        today = store.events(matching: dayPredicate)
            .filter { !$0.isAllDay && !($0.title ?? "").isEmpty }
            .sorted { $0.startDate < $1.startDate }
            .map { ev in
                DayEvent(id: ev.eventIdentifier ?? (ev.title ?? "") + "\(ev.startDate!)",
                         title: ev.title ?? "",
                         start: ev.startDate,
                         end: ev.endDate,
                         attendees: max(0, (ev.attendees?.count ?? 0) - 1))
            }

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
        eventsRevision &+= 1
    }
}
