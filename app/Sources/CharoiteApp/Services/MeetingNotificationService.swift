import Foundation
import UserNotifications

#if os(macOS)
import AppKit

/// Не даёт минутному календарному таймеру показывать одну встречу повторно.
///
/// Храним все показанные id за текущий запуск, а не только последний: две
/// пересекающиеся встречи могут по очереди становиться ближайшими, и первая
/// не должна всплыть снова.
struct MeetingNotificationLedger {
    private(set) var presentedIDs: Set<String> = []

    mutating func claim(_ id: String) -> Bool {
        presentedIDs.insert(id).inserted
    }

    mutating func reset() {
        presentedIDs.removeAll()
    }
}

/// Системное напоминание о встрече.
///
/// Это только сигнал: запись начинается исключительно по отдельному действию
/// «Начать запись». Если уведомления запрещены, полоса внутри окна остаётся
/// рабочим резервным интерфейсом.
@MainActor
final class MeetingNotificationService: NSObject, UNUserNotificationCenterDelegate {
    static let shared = MeetingNotificationService()

    private static let categoryID = "CHAROITE_MEETING_CUE"
    private static let startActionID = "CHAROITE_START_RECORDING"
    private static let dismissActionID = "CHAROITE_NOT_NOW"
    private static let requestPrefix = "charoite.meeting."
    private static let meetingIDKey = "meetingID"

    private let center = UNUserNotificationCenter.current()
    private var ledger = MeetingNotificationLedger()

    private override init() {
        super.init()
    }

    func configure() {
        center.delegate = self
        let start = UNNotificationAction(
            identifier: Self.startActionID,
            title: L.t("Начать запись", "Start recording", "开始录制"),
            options: [.foreground])
        let dismiss = UNNotificationAction(
            identifier: Self.dismissActionID,
            title: L.t("Не сейчас", "Not now", "暂不"),
            options: [])
        center.setNotificationCategories([
            UNNotificationCategory(
                identifier: Self.categoryID,
                actions: [start, dismiss],
                intentIdentifiers: [],
                options: [])
        ])
    }

    /// Вызывается вместе с явным включением календаря в Настройках.
    func requestAuthorization() {
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    func present(_ cue: MeetingCue.Cue) {
        guard ledger.claim(cue.id) else { return }

        let content = UNMutableNotificationContent()
        content.title = L.t("Встреча начинается", "Meeting starting", "会议即将开始")
        content.body = cue.prompt
        content.sound = .default
        content.categoryIdentifier = Self.categoryID
        content.userInfo = [Self.meetingIDKey: cue.id]

        let request = UNNotificationRequest(
            identifier: Self.requestID(for: cue.id),
            content: content,
            trigger: nil)
        center.add(request)
    }

    func remove(cueID: String) {
        let id = Self.requestID(for: cueID)
        center.removePendingNotificationRequests(withIdentifiers: [id])
        center.removeDeliveredNotifications(withIdentifiers: [id])
    }

    func reset() {
        let ids = ledger.presentedIDs.map { Self.requestID(for: $0) }
        center.removePendingNotificationRequests(withIdentifiers: ids)
        center.removeDeliveredNotifications(withIdentifiers: ids)
        ledger.reset()
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler:
            @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound])
    }

    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        let action = response.actionIdentifier
        let meetingID = response.notification.request.content.userInfo[Self.meetingIDKey] as? String
        Task { @MainActor in
            if let meetingID { self.remove(cueID: meetingID) }
            switch action {
            case Self.startActionID:
                CalendarService.shared.dismissCue()
                AppDelegate.showMainWindow()
                SuflerService.shared.start()
            case Self.dismissActionID:
                CalendarService.shared.dismissCue()
            default:
                // Обычный клик по баннеру только открывает приложение.
                // Он не считается согласием на запись.
                AppDelegate.showMainWindow()
            }
        }
        completionHandler()
    }

    private static func requestID(for cueID: String) -> String {
        requestPrefix + Data(cueID.utf8).base64EncodedString()
    }
}

#endif
