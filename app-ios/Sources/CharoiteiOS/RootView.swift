import SwiftUI

/// Три вкладки v1: записать — посмотреть, что собралось, — отметить сделанное.
struct RootView: View {
    var body: some View {
        TabView {
            NavigationStack { RecordView() }
                .tabItem { Label("Запись", systemImage: "mic.fill") }
            NavigationStack { MeetingsView() }
                .tabItem { Label("Встречи", systemImage: "calendar") }
            NavigationStack { GraphTasksView() }
                .tabItem { Label("Задачи", systemImage: "checklist") }
        }
        .tint(Theme.accent)
    }
}

#Preview { RootView() }
