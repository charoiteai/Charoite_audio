import SwiftUI

#if os(macOS)

enum WorkspaceSection: String, CaseIterable, Identifiable {
    case today
    case meeting
    case meetings
    case tasks
    case memory

    var id: String { rawValue }

    var title: String {
        switch self {
        case .today: return L.t("Сегодня", "Today", "今天")
        case .meeting: return L.t("Встреча", "Meeting", "会议")
        case .meetings: return L.t("Встречи", "Meetings", "会议记录")
        case .tasks: return L.t("Задачи", "Tasks", "任务")
        case .memory: return L.t("Память", "Memory", "记忆")
        }
    }

    var icon: String {
        switch self {
        case .today: return "sun.max"
        case .meeting: return "waveform"
        case .meetings: return "rectangle.stack"
        case .tasks: return "checklist"
        case .memory: return "brain.head.profile"
        }
    }
}

/// Одна точка переходов между разделами рабочего стола.
///
/// Меню-бар, карточка встречи и экран подготовки больше не открывают пять
/// независимых окон. Они выбирают раздел здесь и поднимают одно главное окно.
@MainActor
final class WorkspaceNavigation: ObservableObject {
    static let shared = WorkspaceNavigation()

    @Published var selection: WorkspaceSection? = .today
    @Published var selectedMeetingID: String?

    private init() {}

    func open(_ section: WorkspaceSection, meetingID: String? = nil) {
        if let meetingID { selectedMeetingID = meetingID }
        selection = section
        AppDelegate.showMainWindow()
    }
}

#endif
