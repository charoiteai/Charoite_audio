import SwiftUI

/// Три вкладки v1: записать — посмотреть, что собралось, — отметить сделанное.
struct RootView: View {
    var body: some View {
        TabView {
            NavigationStack { RecordView() }
                .tabItem { Label(L.t("Запись", "Record", "录音"), systemImage: "mic.fill") }
            NavigationStack { MeetingsView() }
                .tabItem { Label(L.t("Встречи", "Meetings", "会议"), systemImage: "calendar") }
            NavigationStack { GraphTasksView() }
                .tabItem { Label(L.t("Задачи", "Tasks", "任务"), systemImage: "checklist") }
        }
        .tint(Theme.accent)
    }
}

#Preview { RootView() }
