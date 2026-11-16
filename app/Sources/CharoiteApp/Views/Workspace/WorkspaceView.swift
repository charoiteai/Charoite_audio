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
            VStack(alignment: .leading, spacing: 4) {
                sidebarHeading(L.t("Сейчас", "Now", "当前"))
                sidebarRow(.today)
                sidebarRow(.meeting)

                sidebarHeading(L.t("Библиотека", "Library", "资料库"))
                    .padding(.top, 16)
                sidebarRow(.meetings)
                sidebarRow(.tasks)
                sidebarRow(.memory)

                Spacer(minLength: 20)
                Divider().padding(.horizontal, 10)
                sidebarRow(.inbox)
                    .padding(.vertical, 6)
                // Номер версии — на виду, а не только в строке обновления
                // (просьба владельца 05.09): по нему сверяют, что раскатка дошла.
                Text("Charoite \(VersionStatusService.appVersion)")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 9)
                    .padding(.bottom, 6)
                    .textSelection(.enabled)
                    .accessibilityLabel(Text(L.t("Версия \(VersionStatusService.appVersion)",
                                                  "Version \(VersionStatusService.appVersion)",
                                                  "版本 \(VersionStatusService.appVersion)")))
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

    private func sidebarHeading(_ title: String) -> some View {
        Text(title)
            .font(.caption.weight(.semibold))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .accessibilityAddTraits(.isHeader)
    }

    private func sidebarRow(_ section: WorkspaceSection) -> some View {
        SidebarRow(section: section,
                   isCurrent: (navigation.selection ?? .today) == section,
                   badgeCount: badge(for: section)) {
            if section == .tasks { navigation.selectedTaskMeetingID = nil }
            navigation.selection = section
        }
    }

    private func badge(for section: WorkspaceSection) -> Int {
        switch section {
        case .meeting: return sufler.isRunning ? 1 : 0
        case .meetings:
            return processing.history.filter {
                MeetingProcessingPolicy.resolvedState($0) == .error
            }.count
        case .inbox: return ExternalRecordingPolicy.failedCount(importer.items)
        case .tasks: return tasks.attentionCount
        case .today, .memory: return 0
        }
    }
}

private struct SidebarRow: View {
    let section: WorkspaceSection
    let isCurrent: Bool
    let badgeCount: Int
    let action: () -> Void
    @State private var isHovered = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: section.icon)
                    .frame(width: 20)
                    .foregroundStyle(isCurrent ? Theme.accent : Color.secondary)
                Text(section.title)
                    .font(.callout.weight(isCurrent ? .semibold : .regular))
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .foregroundStyle(isCurrent ? Theme.accent : Color.primary)
                Spacer()
                if badgeCount > 0 {
                    Text("\(badgeCount)")
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 6).padding(.vertical, 1)
                        .background(Capsule().fill(Color.primary.opacity(0.08)))
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: Theme.radius)
                .fill(isCurrent ? Theme.accent.opacity(0.14)
                      : Color.primary.opacity(isHovered ? 0.05 : 0)))
            .contentShape(RoundedRectangle(cornerRadius: Theme.radius))
        }
        .buttonStyle(.plain)
        // бейдж — деталь, имя раздела VoiceOver читает первым
        .accessibilityLabel(Text(section.title))
        .accessibilityValue(badgeCount > 0 ? Text("\(badgeCount)") : Text(""))
        .accessibilityAddTraits(isCurrent ? .isSelected : [])
        .help(section.title)
        .onHover { isHovered = $0 }
    }
}

#endif
