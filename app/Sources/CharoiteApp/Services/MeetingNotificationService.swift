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

/// Нужен ли баннер поверх того, что человек и так видит.
///
/// Полоса внутри окна и баннер несут одно сообщение. Пока окно Charoite
/// открыто и активно, второй экземпляр только мешает; во всех остальных
/// случаях — свёрнуто, перекрыто другой программой, запущено из меню-бара
/// без окна — напоминание и есть смысл функции.
enum MeetingNotificationPolicy {
    static func shouldPresent(appActive: Bool, mainWindowVisible: Bool) -> Bool {
        !(appActive && mainWindowVisible)
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

    // nonisolated: эти константы читает делегат уведомлений, который система
    // зовёт вне главного актора. В Swift 6 обращение к main-actor свойству
    // оттуда — уже ошибка компиляции, а не предупреждение.
    private nonisolated static let categoryID = "CHAROITE_MEETING_CUE"
    private nonisolated static let readyCategoryID = "CHAROITE_MEETING_READY"
    private nonisolated static let startActionID = "CHAROITE_START_RECORDING"
    private nonisolated static let dismissActionID = "CHAROITE_NOT_NOW"
    private nonisolated static let openMeetingActionID = "CHAROITE_OPEN_MEETING"
    private nonisolated static let requestPrefix = "charoite.meeting."
    private nonisolated static let readyRequestPrefix = "charoite.ready."
    private nonisolated static let meetingIDKey = "meetingID"
    private nonisolated static let notePathKey = "notePath"

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
        let openMeeting = UNNotificationAction(
            identifier: Self.openMeetingActionID,
            title: L.t("Открыть встречу", "Open meeting", "打开会议"),
            options: [.foreground])
        center.setNotificationCategories([
            UNNotificationCategory(
                identifier: Self.categoryID,
                actions: [start, dismiss],
                intentIdentifiers: [],
                options: []),
            UNNotificationCategory(
                identifier: Self.readyCategoryID,
                actions: [openMeeting],
                intentIdentifiers: [],
                options: [])
        ])
    }

    /// Вызывается вместе с явным включением календаря в Настройках.
    func requestAuthorization() {
        center.requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    func present(_ cue: MeetingCue.Cue) {
        let visible = NSApplication.shared.windows.contains {
            $0.isVisible && $0.identifier?.rawValue.hasPrefix("main") == true
        }
        guard MeetingNotificationPolicy.shouldPresent(
            appActive: NSApplication.shared.isActive,
            mainWindowVisible: visible
        ) else { return }
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

    func presentReady(_ snapshot: MeetingProcessingSnapshot) {
        guard let notePath = snapshot.notePath else { return }
        let content = UNMutableNotificationContent()
        content.title = L.t("Встреча готова", "Meeting ready", "会议已就绪")
        content.body = L.t("Стенограмма и граф обновлены",
                           "The transcript and graph are updated",
                           "逐字稿和图谱已更新")
        content.sound = .default
        content.categoryIdentifier = Self.readyCategoryID
        content.userInfo = [Self.meetingIDKey: snapshot.meetingID,
                            Self.notePathKey: notePath]
        center.add(UNNotificationRequest(
            identifier: Self.readyRequestPrefix + snapshot.meetingID,
            content: content,
            trigger: nil))
    }

    /// Запись остановилась сама (тишина или потолок длительности).
    ///
    /// Баннер здесь важнее, чем у напоминания о встрече, и показывается всегда:
    /// смысл автостопа — что человека НЕТ у экрана. Полосу статуса в окне он
    /// увидит, когда вернётся, а уведомление останется в центре уведомлений.
    func presentAutostop(reason: String, detail: String) {
        let content = UNMutableNotificationContent()
        content.title = L.t("Запись остановлена", "Recording stopped", "录音已停止")
        content.body = reason == "limit"
            ? L.t("Достигнут потолок длительности — встреча сохранена и разбирается",
                  "Duration ceiling reached — the meeting is saved and being processed",
                  "已达到时长上限——会议已保存并正在处理")
            : L.t("Тишина: встречу никто не ведёт — она сохранена и разбирается",
                  "Silence: nobody is talking — the meeting is saved and being processed",
                  "静音：无人发言——会议已保存并正在处理")
        content.subtitle = detail
        content.sound = .default
        center.add(UNNotificationRequest(identifier: "charoite.autostop." + reason,
                                         content: content, trigger: nil))
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
        let category = response.notification.request.content.categoryIdentifier
        let meetingID = response.notification.request.content.userInfo[Self.meetingIDKey] as? String
        let notePath = response.notification.request.content.userInfo[Self.notePathKey] as? String
        Task { @MainActor in
            if let meetingID {
                if category == Self.readyCategoryID {
                    self.center.removeDeliveredNotifications(
                        withIdentifiers: [Self.readyRequestPrefix + meetingID])
                } else {
                    self.remove(cueID: meetingID)
                }
            }
            switch action {
            case Self.startActionID:
                CalendarService.shared.dismissCue()
                AppDelegate.showMainWindow()
                SuflerService.shared.start()
            case Self.dismissActionID:
                CalendarService.shared.dismissCue()
            case Self.openMeetingActionID:
                if let notePath, FileManager.default.fileExists(atPath: notePath) {
                    NSWorkspace.shared.open(URL(fileURLWithPath: notePath))
                } else {
                    AppDelegate.showMainWindow()
                }
            default:
                if category == Self.readyCategoryID,
                   let notePath, FileManager.default.fileExists(atPath: notePath) {
                    NSWorkspace.shared.open(URL(fileURLWithPath: notePath))
                } else {
                    // Обычный клик по напоминанию только открывает приложение.
                    // Он не считается согласием на запись.
                    AppDelegate.showMainWindow()
                }
            }
        }
        completionHandler()
    }

    private static func requestID(for cueID: String) -> String {
        requestPrefix + Data(cueID.utf8).base64EncodedString()
    }
}

#endif
