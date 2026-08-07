import SwiftUI

#if os(macOS)

/// Подготовка к ближайшей встрече: Чароит помогает ДО разговора.
///
/// После встречи система уже умеет всё — стенограмма, граф, карточка. До
/// встречи человек оставался один: что сегодня в календаре, что мы в прошлый
/// раз обещали по этой теме, какие поручения висят. Этот экран собирает
/// ответы в одном месте за минуту до начала.
struct PrepView: View {
    @ObservedObject private var calendar = CalendarService.shared
    @ObservedObject private var tasks = TasksService.shared
    @ObservedObject private var processing = MeetingProcessingService.shared
    @ObservedObject private var repository = MeetingRepository.shared
    @ObservedObject private var navigation = WorkspaceNavigation.shared
    /// Хвосты по теме ближайшей встречи — из того же поиска, что в окне встреч.
    @State private var topicHits: [MeetingSearch.Hit] = []
    @State private var topicSearchTask: Task<Void, Never>?
    @State private var isLoadingTopic = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    todaySection
                    topicSection
                    tasksSection
                    otherTasksSection
                    lastMeetingSection
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(14)
            }
        }
        .frame(minWidth: 460, minHeight: 380)
        .task {
            tasks.rescan()
            loadTopicTrail()
        }
        .onChange(of: calendar.today) { _, _ in loadTopicTrail() }
        .onDisappear { cancelTopicLoad() }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "sunrise").foregroundStyle(Theme.accent)
            Text(L.t("Подготовка", "Prep", "会前准备")).font(.headline)
            Text(L.t("что сегодня и что мы обещали",
                     "today's meetings and what we promised",
                     "今天的会议与此前的承诺"))
                .font(.caption).foregroundStyle(.secondary)
            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
    }

    // MARK: - Сегодня в календаре

    @ViewBuilder
    private var todaySection: some View {
        section(L.t("Сегодня", "Today", "今天"), icon: "calendar") {
            if calendar.today.isEmpty {
                Text(L.t("Встреч в календаре больше нет — или доступ к календарю не дан.",
                         "No more meetings today — or calendar access was not granted.",
                         "今天没有更多会议——或未授予日历访问权限。"))
                    .font(.callout).foregroundStyle(.secondary)
            } else {
                ForEach(calendar.today) { event in
                    HStack(spacing: 8) {
                        Text(Self.time(event.start)).monospacedDigit()
                            .foregroundStyle(.secondary)
                        Text(event.title).lineLimit(1)
                        if event.attendees > 0 {
                            Text("· \(event.attendees)")
                                .font(.caption).foregroundStyle(.tertiary)
                        }
                        Spacer()
                    }
                    .font(.callout)
                }
            }
        }
    }

    // MARK: - Что было по теме

    @ViewBuilder
    private var topicSection: some View {
        if let next = calendar.today.first {
            section(L.t("Что было по теме «\(short(next.title))»",
                        "Previously on “\(short(next.title))”",
                        "「\(short(next.title))」此前的情况"),
                    icon: "clock.arrow.circlepath") {
                if isLoadingTopic {
                    HStack(spacing: 7) {
                        ProgressView().controlSize(.small)
                        Text(L.t("Ищу связанные встречи…",
                                 "Finding related meetings…",
                                 "正在查找相关会议…"))
                    }
                    .font(.callout).foregroundStyle(.secondary)
                } else if topicHits.isEmpty {
                    Text(L.t("В архиве встреч по этой теме ничего не нашлось.",
                             "Nothing on this topic in the meeting archive.",
                             "会议档案中没有该主题的内容。"))
                        .font(.callout).foregroundStyle(.secondary)
                } else {
                    ForEach(topicHits) { hit in
                        Button {
                            if let meeting = repository.record(matching: hit) {
                                navigation.open(.meetings, meetingID: meeting.id)
                            } else {
                                NSWorkspace.shared.open(hit.file)
                            }
                        } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(hit.title).font(.callout.weight(.medium)).lineLimit(1)
                                Text(hit.snippet).font(.caption)
                                    .foregroundStyle(.secondary).lineLimit(2)
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
    }

    // MARK: - Открытые поручения

    @ViewBuilder
    private var tasksSection: some View {
        let visible = Array(relevantOpenTasks.prefix(5))
        if nextTopic != nil {
            section(L.t("Поручения по теме", "Topic action items", "主题任务"),
                    icon: "checklist") {
                if visible.isEmpty {
                    Text(L.t("Открытых поручений, связанных с этой встречей, не нашлось.",
                             "No open action items linked to this meeting were found.",
                             "未找到与本次会议相关的未完成任务。"))
                        .font(.callout).foregroundStyle(.secondary)
                } else {
                    ForEach(visible) { item in
                        HStack(alignment: .firstTextBaseline, spacing: 6) {
                            Button { tasks.toggle(item) } label: {
                                if tasks.isUpdating(item) {
                                    ProgressView().controlSize(.mini).frame(width: 13, height: 13)
                                } else {
                                    Image(systemName: "square").foregroundStyle(.secondary)
                                }
                            }
                            .buttonStyle(.plain).disabled(tasks.isUpdating(item))
                            Text(MarkdownLine.render(item.text)).font(.callout).lineLimit(2)
                        }
                    }
                    if relevantOpenTasks.count > visible.count {
                        Button {
                            navigation.openTasks()
                        } label: {
                            Text(L.t("и ещё \(relevantOpenTasks.count - visible.count) по теме",
                                     "and \(relevantOpenTasks.count - visible.count) more on this topic",
                                     "另有 \(relevantOpenTasks.count - visible.count) 项相关任务"))
                        }
                        .charoite(.link, .s)
                    }
                }
            }
        }
    }

    /// Общие хвосты не выдаём за обязательства ближайшей встречи: они
    /// остаются доступны отдельным блоком и ведут в полный список задач.
    @ViewBuilder
    private var otherTasksSection: some View {
        if otherOpenTaskCount > 0 {
            section(L.t("Другие открытые поручения", "Other open action items", "其他未完成任务"),
                    icon: "tray.full") {
                Button {
                    navigation.openTasks()
                } label: {
                    HStack(spacing: 6) {
                        Text(L.t("\(otherOpenTaskCount) в общем списке",
                                 "\(otherOpenTaskCount) in the full list",
                                 "完整列表中有 \(otherOpenTaskCount) 项"))
                        Image(systemName: "chevron.right")
                            .font(.caption2).foregroundStyle(.tertiary)
                    }
                }
                .buttonStyle(.plain)
            }
        }
    }

    // MARK: - Прошлая встреча

    @ViewBuilder
    private var lastMeetingSection: some View {
        if let last = relevantHistoryMeeting {
            section(L.t("Прошлая встреча по теме", "Last meeting on this topic", "该主题的上次会议"),
                    icon: "clock") {
                Button {
                    navigation.open(.meetings, meetingID: last.meetingID)
                } label: {
                    HStack(spacing: 6) {
                        Text(last.title).font(.callout.weight(.medium)).lineLimit(1)
                        Image(systemName: "chevron.right")
                            .font(.caption2).foregroundStyle(.tertiary)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func section(_ title: String, icon: String,
                         @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Label(title, systemImage: icon)
                .font(.subheadline.weight(.semibold))
            content()
        }
    }

    private func loadTopicTrail() {
        topicSearchTask?.cancel()
        guard let graph = AppSettings.graphDir,
              let next = calendar.today.first else {
            topicHits = []
            isLoadingTopic = false
            return
        }
        let query = PrepPolicy.titleQuery(next.title)
        guard !query.isEmpty else {
            topicHits = []
            isLoadingTopic = false
            return
        }
        topicHits = []
        isLoadingTopic = true
        topicSearchTask = Task { @MainActor in
            let hits = await MeetingSearch.searchAsync(query, graph: graph, limit: 3)
            guard !Task.isCancelled, nextTopic == query else { return }
            topicHits = hits
            isLoadingTopic = false
        }
    }

    private func cancelTopicLoad() {
        topicSearchTask?.cancel()
        topicSearchTask = nil
        isLoadingTopic = false
    }

    private var nextTopic: String? {
        guard let title = calendar.today.first?.title else { return nil }
        let query = PrepPolicy.titleQuery(title)
        return query.isEmpty ? nil : query
    }

    private var relatedDays: Set<String> {
        Set(topicHits.map(\.day))
    }

    private var relevantOpenTasks: [TasksService.Item] {
        guard let topic = nextTopic else { return [] }
        return tasks.items.filter {
            !$0.done && PrepPolicy.matchesTopic(
                text: $0.text, source: $0.rel, topic: topic, relatedDays: relatedDays)
        }
    }

    private var otherOpenTaskCount: Int {
        max(0, tasks.openCount - relevantOpenTasks.count)
    }

    private var relevantHistoryMeeting: MeetingProcessingSnapshot? {
        guard let topic = nextTopic else { return nil }
        return processing.history.first {
            PrepPolicy.matchesTopic(
                text: $0.title,
                source: $0.meetingID + " " + $0.transcriptPath,
                topic: topic,
                relatedDays: relatedDays)
        }
    }

    /// Название события без хвостов вида «(еженедельно)» — для заголовка.
    private func short(_ title: String) -> String {
        PrepPolicy.titleQuery(title)
    }

    static func time(_ date: Date) -> String {
        let f = DateFormatter()
        f.locale = L.locale
        f.dateFormat = "HH:mm"
        return f.string(from: date)
    }
}
#endif
