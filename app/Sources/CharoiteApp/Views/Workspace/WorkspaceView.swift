import SwiftUI

#if os(macOS)

struct WorkspaceView: View {
    @ObservedObject private var navigation = WorkspaceNavigation.shared
    @ObservedObject private var tasks = TasksService.shared
    @ObservedObject private var sufler = SuflerService.shared
    @ObservedObject private var processing = MeetingProcessingService.shared
    @ObservedObject private var importer = ImportService.shared

    var body: some View {
        NavigationSplitView {
            // Выбор раздела — кнопки БЕЗ контейнера прокрутки. История трёх
            // заходов: List в sidebar-колонке терял клики (04.08) → кнопки в
            // ScrollView → на 0.66.0 живой AX-прогон (01.09) поймал СИМПТОМ:
            // строки рисовались на две ниже своих хитов (клик по видимой
            // «Встреча» выбирал «Задачи», нижние — вне интерактивной зоны).
            // VStack проверен тем же щупом на этой сборке: 5/5 строк
            // открывают сами себя. Если съезд вернётся — первым делом
            // смотреть инсет sidebar-КОЛОНКИ (titlebar/safe area), а не
            // контейнер (GLM r1 по #477); и при росте секций за ~12 сюда
            // должен вернуться скролл вместе с нейтрализацией инсета (DS).
            VStack(spacing: 2) {
                ForEach(WorkspaceSection.allCases) { section in
                    sidebarRow(section)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 10)
            .padding(.top, 8)
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
        case .inbox:
            ExternalRecordingView()
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
        case .inbox: return ExternalRecordingPolicy.failedCount(importer.items)
        case .tasks: return tasks.openCount
        case .today, .memory: return 0
        }
    }
}

#endif
