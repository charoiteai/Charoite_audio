import SwiftUI

#if os(macOS)

/// Единый список поручений из Markdown с обратной связью к встречам.
struct TasksView: View {
    @ObservedObject private var tasks = TasksService.shared
    @ObservedObject private var repository = MeetingRepository.shared
    @ObservedObject private var navigation = WorkspaceNavigation.shared
    @State private var showDone = false
    @State private var query = ""

    var body: some View {
        VStack(spacing: 0) {
            header
            if let error = tasks.mutationError {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 12).padding(.bottom, 8)
            }
            Divider()
            content
        }
        .frame(minWidth: 520, minHeight: 360)
        .onAppear { tasks.rescan() }
    }

    private var header: some View {
        VStack(spacing: 8) {
            HStack(spacing: 10) {
                Image(systemName: "checklist").foregroundStyle(Theme.accent)
                Text(L.t("Задачи со встреч", "Meeting tasks", "会议任务"))
                    .font(.headline).fixedSize()
                Text(L.t("\(scopedOpenCount) открытых",
                         "\(scopedOpenCount) open",
                         "\(scopedOpenCount) 项未完成"))
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                TextField(L.t("Найти поручение", "Find an action item", "查找任务"), text: $query)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 210)
                Toggle(isOn: $showDone) {
                    Text(L.t("Сделанные", "Completed", "已完成")).fixedSize()
                }
                .toggleStyle(.checkbox).font(.caption)
                Button { copyVisibleOpen() } label: { Image(systemName: "doc.on.doc") }
                    .help(L.t("Скопировать видимые открытые задачи",
                              "Copy visible open tasks",
                              "复制当前显示的未完成任务"))
                    .disabled(visibleOpen.isEmpty)
                Button { tasks.rescan() } label: { Image(systemName: "arrow.clockwise") }
                    .help(L.t("Перечитать граф", "Rescan the graph", "重新扫描图谱"))
            }
            if let meeting = selectedMeeting {
                HStack(spacing: 7) {
                    Image(systemName: "line.3.horizontal.decrease.circle.fill")
                        .foregroundStyle(Theme.accent)
                    Text(L.t("Только: \(meeting.title)",
                             "Only: \(meeting.title)",
                             "仅显示：\(meeting.title)"))
                        .font(.caption.weight(.medium)).lineLimit(1)
                    Button {
                        navigation.selectedTaskMeetingID = nil
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                    }
                    .charoite(.icon, .s)
                    .help(L.t("Показать все поручения", "Show all action items", "显示全部任务"))
                    Spacer()
                    Button(L.t("Открыть встречу", "Open meeting", "打开会议")) {
                        navigation.open(.meetings, meetingID: meeting.id)
                    }
                    .charoite(.link, .s)
                }
                .padding(.horizontal, 8).padding(.vertical, 5)
                .background(RoundedRectangle(cornerRadius: 7).fill(Theme.accent.opacity(0.09)))
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 9)
    }

    @ViewBuilder
    private var content: some View {
        if visible.isEmpty {
            VStack(spacing: 10) {
                Image(systemName: emptyIcon).font(.largeTitle).foregroundStyle(.quaternary)
                Text(emptyText)
                    .font(.subheadline).foregroundStyle(.tertiary)
                    .multilineTextAlignment(.center)
                if navigation.selectedTaskMeetingID != nil {
                    Button(L.t("Показать все задачи", "Show all tasks", "显示全部任务")) {
                        navigation.selectedTaskMeetingID = nil
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            List {
                ForEach(groups, id: \.rel) { group in
                    Section {
                        ForEach(group.items) { item in row(item) }
                    } header: {
                        groupHeader(group)
                    }
                }
            }
            .listStyle(.inset)
        }
    }

    private var selectedMeeting: MeetingRecord? {
        repository.record(id: navigation.selectedTaskMeetingID)
    }

    private var scoped: [TasksService.Item] {
        guard let meetingID = navigation.selectedTaskMeetingID else { return tasks.items }
        return tasks.items(for: meetingID)
    }

    private var scopedOpenCount: Int { scoped.filter { !$0.done }.count }

    private var visible: [TasksService.Item] {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        return scoped.filter { item in
            (showDone || !item.done)
                && (trimmed.isEmpty
                    || item.text.localizedCaseInsensitiveContains(trimmed)
                    || TasksService.sourceTitle(item.rel).localizedCaseInsensitiveContains(trimmed))
        }
    }

    private var visibleOpen: [TasksService.Item] { visible.filter { !$0.done } }

    private var groups: [(rel: String, items: [TasksService.Item])] {
        var order: [String] = []
        var byRel: [String: [TasksService.Item]] = [:]
        for item in visible {
            if byRel[item.rel] == nil { order.append(item.rel) }
            byRel[item.rel, default: []].append(item)
        }
        return order.map { (rel: $0, items: byRel[$0] ?? []) }
    }

    private func groupHeader(_ group: (rel: String, items: [TasksService.Item])) -> some View {
        HStack(spacing: 6) {
            Text(TasksService.sourceTitle(group.rel))
                .font(.caption).foregroundStyle(.secondary).lineLimit(1)
            // Дата встречи, а не файла: список идёт от свежих к ранним, и без
            // подписи непонятно, поручение этой недели или трёхнедельной давности.
            if let when = TasksService.meetingDate(group.rel) {
                Text(Self.dayFormatter.string(from: when))
                    .font(.caption2.monospacedDigit()).foregroundStyle(.tertiary)
                    .accessibilityLabel(Self.voiceOverFormatter.string(from: when))
            }
            Spacer()
            if let first = group.items.first,
               let meeting = repository.record(matching: first) {
                Button {
                    navigation.open(.meetings, meetingID: meeting.id)
                } label: {
                    Label(L.t("К встрече", "Meeting", "会议"), systemImage: "arrow.up.right.square")
                }
                .charoite(.link, .s)
                .help(L.t("Открыть карточку встречи", "Open the meeting card", "打开会议卡片"))
            }
        }
    }

    private func row(_ item: TasksService.Item) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Button { tasks.toggle(item) } label: {
                if tasks.isUpdating(item) {
                    ProgressView().controlSize(.mini).frame(width: 14, height: 14)
                } else {
                    Image(systemName: item.done ? "checkmark.square.fill" : "square")
                        .foregroundStyle(item.done ? Theme.accent : Color.secondary)
                }
            }
            .buttonStyle(.plain)
            .disabled(tasks.isUpdating(item))
            Text(MarkdownLine.render(item.text))
                .strikethrough(item.done)
                .foregroundStyle(item.done ? .secondary : .primary)
            Spacer(minLength: 8)
            // Срок читается из самой строки markdown (TaskDue), файл не
            // меняется. Просрочка в общем потоке текста не видна — глаз
            // цепляется за форму, а не за «до 24.07» в конце фразы. У
            // сделанного чип не показываем: сроку нечего требовать.
            if !item.done, let due = TaskDue.parse(item.text) {
                // fixedSize + приоритет: без них длинный текст поручения
                // забирает всю ширину строки, чипу достаётся ноль, и он
                // просто не рисуется — ровно так он и не появился на первой
                // проверке живьём. Переносится текст, срок остаётся целым.
                DueChip(due: due)
                    .fixedSize()
                    .layoutPriority(1)
            }
        }
        .padding(.vertical, 2)
        .contextMenu {
            if let meeting = repository.record(matching: item) {
                Button(L.t("Открыть встречу", "Open meeting", "打开会议")) {
                    navigation.open(.meetings, meetingID: meeting.id)
                }
            }
            Button(L.t("Открыть Markdown", "Open Markdown", "打开 Markdown")) {
                NSWorkspace.shared.open(item.file)
            }
        }
    }

    private var emptyIcon: String {
        query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? "checkmark.circle" : "magnifyingglass"
    }

    private var emptyText: String {
        if !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return L.t("По этому запросу поручений нет.",
                       "No action items match this search.",
                       "没有符合搜索条件的任务。")
        }
        if scopedOpenCount == 0 && !scoped.isEmpty {
            return L.t("Всё сделано", "All done", "全部完成")
        }
        return L.t("Поручения из минуток появятся здесь.\nMarkdown остаётся источником истины.",
                   "Action items from minutes appear here.\nMarkdown remains the source of truth.",
                   "纪要中的任务会显示在这里。\nMarkdown 仍是真实来源。")
    }

    private func copyVisibleOpen() {
        let text = visibleOpen.map { "- [ ] \($0.text)" }.joined(separator: "\n")
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    /// «5 авг» — коротко, чтобы не спорить за ширину с названием встречи.
    private static let dayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale.current
        f.setLocalizedDateFormatFromTemplate("d MMM")
        return f
    }()

    /// VoiceOver читает сокращения плохо: ему — полная дата.
    private static let voiceOverFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale.current
        f.dateStyle = .long
        f.timeStyle = .none
        return f
    }()
}

#endif
