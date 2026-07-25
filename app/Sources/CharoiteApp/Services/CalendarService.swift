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
    private let store = EKEventStore()
    private var timer: Timer?

    /// Запросить доступ (системный диалог) и начать следить за ближайшим
    /// событием. Отказ пользователя — тихо выключаемся.
    func enable() {
        let done: (Bool) -> Void = { granted in
            Task { @MainActor in
                guard granted else { self.nextEventTitle = nil; return }
                self.refresh()
                self.timer?.invalidate()
                self.timer = Timer.scheduledTimer(withTimeInterval: 300, repeats: true) { _ in
                    Task { @MainActor in self.refresh() }
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
    }

    /// Событие в окне «идёт сейчас или начнётся в ближайший час».
    private func refresh() {
        let now = Date()
        let predicate = store.predicateForEvents(
            withStart: now.addingTimeInterval(-5 * 60),
            end: now.addingTimeInterval(60 * 60),
            calendars: nil)
        let events = store.events(matching: predicate)
            .filter { !$0.isAllDay && !($0.title ?? "").isEmpty }
            .sorted { $0.startDate < $1.startDate }
        nextEventTitle = events.first?.title
    }
}
