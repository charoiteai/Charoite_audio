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
    /// Тот же ключ, что в Настройках и библиотеке: подключение календаря
    /// отсюда обязано отразиться там, а не жить третьим флагом.
    @AppStorage("charoite.calendarBriefs") private var calendarBriefs = false
    /// Хвосты по теме ближайшей встречи — из того же поиска, что в окне встреч.
    @State private var topicHits: [MeetingSearch.Hit] = []
    @State private var topicSearchTask: Task<Void, Never>?
    @State private var isLoadingTopic = false
    /// Сводка долгов и три горящих — считаются при изменении поручений или
    /// темы, а не в теле вью: три прохода с разбором срока по сотням строк
    /// на каждый рендер — лишняя работа на главном потоке (ревью 22.08,
    /// DeepSeek).
    @State private var debts = Debts()

    struct Debts: Equatable {
        var summary = ""
        var urgent: [TasksService.Item] = []
        var isEmpty: Bool { summary.isEmpty }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    todaySection
                    topicSection
                    tasksSection
                    debtsSection
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
            recomputeDebts()
        }
        .onChange(of: calendar.today) { _, _ in loadTopicTrail() }
        .onChange(of: tasks.items) { _, _ in recomputeDebts() }
        .onChange(of: topicHits) { _, _ in recomputeDebts() }
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
                // Три разных состояния — три разных действия. Одна строка
                // «больше нет — или доступ не дан» не говорила, что нажать
                // (правило 2 ревизии; дизайн-аудит 21.08).
                // .none/.some явно: Swift 5.10 на раннере CI не считает
                // `case nil / false / true` по Bool? исчерпывающим.
                switch calendar.accessGranted {
                case .none:
                    EmptyState(title: L.t("Календарь не подключён", "Calendar is not connected", "日历未连接"),
                               text: L.t("Charoite читает только названия и время событий — чтобы за минуту до встречи собрать, что было по теме и что вы обещали. Локально, ничего не пишет.",
                                         "Charoite reads only event titles and times — to gather, a minute before the meeting, what happened on the topic and what you promised. Local, write-free.",
                                         "Charoite 仅读取日程标题与时间——在会前一分钟整理该主题的往事与你的承诺。本地运行，不做写入。"),
                               inset: false) {
                        Button(L.t("Подключить календарь", "Connect calendar", "连接日历")) {
                            calendarBriefs = true
                            calendar.enable(askForNotifications: true)
                        }
                        .charoite(.regular, .s)
                    }
                case .some(false):
                    EmptyState(title: L.t("Доступа к календарю нет", "No calendar access", "没有日历访问权限"),
                               text: L.t("macOS отклонил запрос. Разрешите доступ в Системных настройках → Конфиденциальность и безопасность → Календари — и вернитесь сюда.",
                                         "macOS declined the request. Allow access in System Settings → Privacy & Security → Calendars, then come back.",
                                         "macOS 拒绝了请求。请在「系统设置 → 隐私与安全性 → 日历」中允许访问，然后回到这里。"),
                               inset: false) {
                        Button(L.t("Открыть Системные настройки", "Open System Settings", "打开系统设置")) {
                            if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars") {
                                NSWorkspace.shared.open(url)
                            }
                        }
                        .charoite(.regular, .s)
                    }
                case .some(true):
                    Text(L.t("Встреч в календаре больше нет — день вокруг того, что вы обещали.",
                             "No more meetings today — the day is about what you promised.",
                             "今天没有更多会议——这一天围绕你的承诺。"))
                        .font(.callout).foregroundStyle(.secondary)
                }
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
    /// остаются отдельным блоком и ведут в полный список задач.
    ///
    /// «25 в общем списке» ревизия 08.08 приводила как пример числа без
    /// срока: теперь — сводка по корзинам `TasksScreenPolicy` (те же, что на
    /// экране задач), а без встречи в календаре — ещё и три самых горящих
    /// поручения с чипом срока: день собирается вокруг долгов, а не вокруг
    /// отсутствующего события (дизайн-аудит 21.08, ход 3).
    @ViewBuilder
    private var debtsSection: some View {
        if !debts.isEmpty {
            section(nextTopic == nil
                        ? L.t("Что вы обещали", "What you promised", "你的承诺")
                        : L.t("Другие открытые поручения", "Other open action items", "其他未完成任务"),
                    icon: "tray.full") {
                Button {
                    navigation.openTasks()
                } label: {
                    HStack(spacing: 6) {
                        Text(debts.summary)
                        Image(systemName: "chevron.right")
                            .font(.caption2).foregroundStyle(.tertiary)
                    }
                }
                .buttonStyle(.plain)
                .font(.callout)
                if nextTopic == nil {
                    ForEach(debts.urgent) { item in
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
                            Spacer(minLength: 4)
                            if let due = TaskDue.parse(item.text) { DueChip(due: due) }
                        }
                    }
                }
            }
        }
    }

    private func recomputeDebts() {
        let open = otherOpenTasks
        debts = Debts(summary: open.isEmpty ? "" : Self.debtsSummary(open.map(\.text)),
                      urgent: nextTopic == nil ? Self.mostUrgent(open, limit: 3) : [])
    }

    /// «2 просрочено · 6 на этой неделе · 15 без срока» — нули не пишем.
    static func debtsSummary(_ texts: [String], now: Date = Date()) -> String {
        var counts: [TasksScreenPolicy.DueBucket: Int] = [:]
        for text in texts {
            counts[TasksScreenPolicy.bucket(text: text, done: false, now: now), default: 0] += 1
        }
        let parts: [(TasksScreenPolicy.DueBucket, String, String, String)] = [
            (.overdue, "просрочено", "overdue", "已逾期"),
            (.week, "на этой неделе", "this week", "本周"),
            (.later, "позже", "later", "稍后"),
            (.undated, "без срока", "undated", "无期限"),
        ]
        let shown = parts.compactMap { bucket, ru, en, zh -> String? in
            guard let n = counts[bucket], n > 0 else { return nil }
            return "\(n) " + L.t(ru, en, zh)
        }
        return shown.joined(separator: " · ")
    }

    /// Горящее сверху: просроченные, потом неделя, потом остальное.
    static func mostUrgent(_ items: [TasksService.Item], limit: Int,
                           now: Date = Date()) -> [TasksService.Item] {
        let ranked = items.map { item -> (TasksScreenPolicy.DueBucket, Int, TasksService.Item) in
            let bucket = TasksScreenPolicy.bucket(text: item.text, done: false, now: now)
            // внутри просроченных — самые давние первыми
            var days = 0
            if case .overdue(let d)? = TaskDue.parse(item.text)?.status(now: now) { days = -d }
            return (bucket, days, item)
        }
        // Третий ключ — стабильность: при равных корзинах порядок файла,
        // а не произвол нестабильной сортировки (ревью 22.08, DeepSeek).
        return ranked
            .sorted { ($0.0, $0.1, $0.2.id) < ($1.0, $1.1, $1.2.id) }
            .prefix(limit)
            .map(\.2)
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

    /// Открытые поручения вне темы ближайшей встречи.
    private var otherOpenTasks: [TasksService.Item] {
        let relevant = Set(relevantOpenTasks.map(\.id))
        return tasks.items.filter { !$0.done && !relevant.contains($0.id) }
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
