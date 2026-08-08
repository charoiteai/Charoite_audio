import SwiftUI

#if os(macOS)

struct WorkspaceView: View {
    @ObservedObject private var navigation = WorkspaceNavigation.shared
    @ObservedObject private var tasks = TasksService.shared
    @ObservedObject private var sufler = SuflerService.shared
    @ObservedObject private var processing = MeetingProcessingService.shared

    var body: some View {
        NavigationSplitView {
            // Выбор раздела — кнопки в обычном ScrollView, без List вовсе.
            // List в sidebar-колонке этого окна молча терял и подсветку, и
            // клики (AX: selected=false после клика) — найдено живым прогоном
            // 04.08; кнопки же в этом окне срабатывают всегда.
            ScrollView {
                VStack(spacing: 2) {
                    ForEach(WorkspaceSection.allCases) { section in
                        sidebarRow(section)
                    }
                }
                .padding(.horizontal, 10)
                .padding(.top, 8)
            }
            .navigationTitle("Charoite")
            // Ширину колонки задаём явно. Без неё второе окно той же сцены
            // открывалось со схлопнутым сайдбаром, а detail оставался
            // нарисованным по прежней ширине: подписи разделов пропадали, а
            // кнопка и правая колонка уезжали за край окна (найдено живым
            // сравнением двух окон 08.08).
            .navigationSplitViewColumnWidth(min: 170, ideal: 200, max: 260)
        } detail: {
            detail
                .navigationTitle((navigation.selection ?? .today).title)
                // Detail не должен требовать больше, чем ему дали: иначе
                // содержимое не сжимается, а выпирает за границу окна.
                .frame(minWidth: 640, maxWidth: .infinity, maxHeight: .infinity)
        }
        .navigationSplitViewStyle(.balanced)
        // 900, а не 980: с сайдбаром в 200 прежний минимум требовал окна
        // шире 1180 — ровно того, в котором приложение и открывается.
        .frame(minWidth: 900, minHeight: 620)
        .onAppear {
            tasks.rescan()
            MeetingProcessingService.shared.startMonitoring()
        }
        // Готовая обработка создаёт Минутки.md с чекбоксами. Без повторного
        // скана открытый раздел задач оставался старым до ручного обновления.
        .onChange(of: processing.history) { _, _ in tasks.rescan() }
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

    private func sidebarRow(_ section: WorkspaceSection) -> some View {
        let isCurrent = (navigation.selection ?? .today) == section
        let count = badge(for: section)
        return Button {
            if section == .tasks { navigation.selectedTaskMeetingID = nil }
            navigation.selection = section
        } label: {
            HStack(spacing: 8) {
                Image(systemName: section.icon)
                    .frame(width: 20)
                    .foregroundStyle(isCurrent ? Theme.accent : Color.secondary)
                Text(section.title)
                    .foregroundStyle(isCurrent ? Theme.accent : Color.primary)
                Spacer()
                if count > 0 {
                    Text("\(count)")
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 6).padding(.vertical, 1)
                        .background(Capsule().fill(Color.primary.opacity(0.08)))
                }
            }
            .padding(.horizontal, 9)
            .padding(.vertical, 6)
            .background(RoundedRectangle(cornerRadius: 6)
                .fill(isCurrent ? Theme.accent.opacity(0.14) : Color.clear))
            .contentShape(RoundedRectangle(cornerRadius: 6))
        }
        .buttonStyle(.plain)
        // бейдж — деталь, имя раздела VoiceOver читает первым
        .accessibilityLabel(Text(section.title))
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
