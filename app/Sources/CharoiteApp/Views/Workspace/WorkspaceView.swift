import SwiftUI

#if os(macOS)

struct WorkspaceView: View {
    @ObservedObject private var navigation = WorkspaceNavigation.shared
    @ObservedObject private var tasks = TasksService.shared
    @ObservedObject private var sufler = SuflerService.shared
    @ObservedObject private var processing = MeetingProcessingService.shared

    var body: some View {
        NavigationSplitView {
            List(WorkspaceSection.allCases, selection: $navigation.selection) { section in
                Label(section.title, systemImage: section.icon)
                    .tag(section)
                    .badge(badge(for: section))
            }
            .navigationTitle("Charoite")
            .frame(minWidth: 170)
        } detail: {
            detail
                .navigationTitle((navigation.selection ?? .today).title)
        }
        .frame(minWidth: 980, minHeight: 640)
        .onAppear {
            tasks.rescan()
            MeetingProcessingService.shared.startMonitoring()
        }
    }

    @ViewBuilder
    private var detail: some View {
        switch navigation.selection ?? .today {
        case .today:
            TodayWorkspaceView()
        case .meeting:
            SuflerView()
        case .meetings:
            MeetingLibraryView()
        case .tasks:
            TasksView()
        case .memory:
            LocalChatView()
        }
    }

    private func badge(for section: WorkspaceSection) -> Int {
        switch section {
        case .meeting: return sufler.isRunning ? 1 : 0
        case .meetings:
            return processing.history.filter {
                MeetingProcessingPolicy.resolvedState($0) == .error
            }.count
        case .tasks: return tasks.openCount
        case .today, .memory: return 0
        }
    }
}

#endif
