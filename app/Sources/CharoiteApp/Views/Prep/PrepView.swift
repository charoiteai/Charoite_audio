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
    /// Хвосты по теме ближайшей встречи — из того же поиска, что в окне встреч.
    @State private var topicHits: [MeetingSearch.Hit] = []
    @State private var cardMeeting: MeetingProcessingSnapshot?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    todaySection
                    topicSection
                    tasksSection
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
        .sheet(item: $cardMeeting) { MeetingCardView(meeting: $0) }
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
                if topicHits.isEmpty {
                    Text(L.t("В архиве встреч по этой теме ничего не нашлось.",
                             "Nothing on this topic in the meeting archive.",
                             "会议档案中没有该主题的内容。"))
                        .font(.callout).foregroundStyle(.secondary)
                } else {
                    ForEach(topicHits) { hit in
                        Button {
                            NSWorkspace.shared.open(hit.file)
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
        let open = tasks.items.filter { !$0.done }.prefix(5)
        if !open.isEmpty {
            section(L.t("Открытые поручения", "Open action items", "未完成任务"),
                    icon: "checklist") {
                ForEach(Array(open)) { item in
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Image(systemName: "circle").font(.caption2)
                            .foregroundStyle(.secondary)
                        Text(item.text).font(.callout).lineLimit(2)
                    }
                }
                if tasks.openCount > 5 {
                    Text(L.t("и ещё \(tasks.openCount - 5) в окне задач",
                             "and \(tasks.openCount - 5) more in the tasks window",
                             "任务窗口中还有 \(tasks.openCount - 5) 项"))
                        .font(.caption).foregroundStyle(.tertiary)
                }
            }
        }
    }

    // MARK: - Прошлая встреча

    @ViewBuilder
    private var lastMeetingSection: some View {
        if let last = processing.history.first {
            section(L.t("Прошлая встреча", "Last meeting", "上次会议"),
                    icon: "clock") {
                Button {
                    cardMeeting = last
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
        guard let graph = AppSettings.graphDir,
              let next = calendar.today.first else {
            topicHits = []
            return
        }
        let query = PrepPolicy.titleQuery(next.title)
        guard !query.isEmpty else { topicHits = []; return }
        topicHits = Array(MeetingSearch.search(query, graph: graph).prefix(3))
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
