import SwiftUI

#if os(macOS)

/// Единый список поручений из Markdown с обратной связью к встречам.
extension TasksGrouping {
    var title: String {
        switch self {
        case .byMeeting: return L.t("По встрече", "By meeting", "按会议")
        case .byDue: return L.t("По сроку", "By due date", "按期限")
        }
    }
}

extension TasksScreenPolicy.DueBucket {
    var title: String {
        switch self {
        case .overdue: return L.t("Просрочено", "Overdue", "已逾期")
        case .week: return L.t("Ближайшие 7 дней", "Next 7 days", "未来 7 天")
        case .later: return L.t("Позже", "Later", "更晚")
        case .undated: return L.t("Без срока", "No due date", "无期限")
        case .done: return L.t("Сделанные", "Completed", "已完成")
        }
    }
}

/// Секции «Мои» и «Старые» — поверх обеих группировок (запрос владельца
/// 01.09: «за мной — первым, даже если задачи далеко; старьё — почистить»).
private enum TasksMineStale {
    /// Кэш на процесс: configValue читает config.yaml с диска, а owner
    /// дёргался из трёх computed-секций на каждую букву поиска (GLM r1).
    static let owner: String = AppSettings.configValue("user_name") ?? ""
    static let mineTitle = L.t("Мои", "Mine", "我的")
    static func staleTitle(_ n: Int) -> String {
        L.t("Старые (\(n))", "Stale (\(n))", "旧任务（\(n)）")
    }
}

struct TasksView: View {
    @ObservedObject private var tasks = TasksService.shared
    @ObservedObject private var repository = MeetingRepository.shared
    @ObservedObject private var navigation = WorkspaceNavigation.shared
    @State private var showDone = false
    @State private var query = ""
    @AppStorage("tasks.grouping") private var groupingRaw = TasksGrouping.byMeeting.rawValue
    private var grouping: TasksGrouping { TasksGrouping(rawValue: groupingRaw) ?? .byMeeting }

    var body: some View {
        VStack(spacing: 0) {
            header
            if let error = tasks.mutationError {
                Label(error, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(Theme.warning)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 12).padding(.bottom, 8)
            }
            Divider()
            content
        }
        // 640, не 520: шапка с сводкой и переключателем режима при 520
        // обрезала правые контролы (круг по PR #367, DeepSeek)
        .frame(minWidth: 640, minHeight: 360)
        .onAppear {
            tasks.rescan()
            // мусор в UserDefaults рендерился дефолтом, но Picker не
            // подсвечивал ни один сегмент — контрол и логика расходились
            if TasksGrouping(rawValue: groupingRaw) == nil {
                groupingRaw = TasksGrouping.byMeeting.rawValue
            }
        }
    }

    private var header: some View {
        VStack(spacing: 8) {
            HStack(spacing: 10) {
                Image(systemName: "checklist").foregroundStyle(Theme.accent)
                Text(L.t("Задачи со встреч", "Meeting tasks", "会议任务"))
                    .font(.headline).fixedSize()
                // Сводка вместо голого счётчика (макет MOBILE_2026-08):
                // просрочка оранжевым, три числа не пересекаются.
                summaryLine
                Spacer()
                Picker("", selection: $groupingRaw) {
                    ForEach(TasksGrouping.allCases, id: \.rawValue) { mode in
                        Text(mode.title).tag(mode.rawValue)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
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
            EmptyState(title: emptyTitle, text: emptyText, systemImage: emptyIcon) {
                if navigation.selectedTaskMeetingID != nil {
                    Button(L.t("Показать все задачи", "Show all tasks", "显示全部任务")) {
                        navigation.selectedTaskMeetingID = nil
                    }
                    .charoite(.regular, .s)
                } else if !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    Button(L.t("Сбросить поиск", "Clear search", "清除搜索")) { query = "" }
                        .charoite(.regular, .s)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if grouping == .byDue {
            List {
                mineSection
                // «Старые» стоят ДО «Сделанных»: свёрнутые открытые не
                // должны лежать ниже закрытых (DS r1 по #475).
                ForEach(dueGroups.filter { $0.bucket != .done }, id: \.bucket) { group in
                    Section {
                        // Источник — в строке: в корзинах срока встречи
                        // перемешаны, и без подписи поручение безлико.
                        ForEach(group.items) { item in row(item, showSource: true) }
                    } header: {
                        Text(group.bucket.title)
                            .font(.caption)
                            .foregroundStyle(group.bucket == .overdue
                                             ? AnyShapeStyle(Theme.overdue)
                                             : AnyShapeStyle(.secondary))
                    }
                }
                staleSection
                ForEach(dueGroups.filter { $0.bucket == .done }, id: \.bucket) { group in
                    Section {
                        ForEach(group.items) { item in row(item, showSource: true) }
                    } header: {
                        Text(group.bucket.title).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .listStyle(.inset)
        } else {
            List {
                mineSection
                ForEach(groups, id: \.rel) { group in
                    Section {
                        ForEach(group.items) { item in row(item) }
                    } header: {
                        groupHeader(group)
                    }
                }
                staleSection
            }
            .listStyle(.inset)
        }
    }

    private var summaryLine: some View {
        // Сводка — статус ВСЕЙ выборки встречи, поиск — временная линза:
        // та же семантика, что у прежнего «N открытых» (scopedOpenCount не
        // знал query). Считать по visible значило бы обнулять «сделано»
        // при выключенном тумблере (круг по PR #367: qwen + DeepSeek,
        // решение зафиксировано).
        let s = TasksScreenPolicy.summary(scoped.map { ($0.text, $0.done) })
        return HStack(spacing: 4) {
            if s.overdue > 0 {
                Text(L.t("\(s.overdue) просрочено", "\(s.overdue) overdue",
                         "\(s.overdue) 已逾期"))
                    .foregroundStyle(Theme.overdue)
                Text("·").foregroundStyle(.quaternary)
            }
            Text(L.t("\(s.open) открыто", "\(s.open) open", "\(s.open) 未完成"))
                .foregroundStyle(.secondary)
            let staleN = splitOpen.stale.count
            if staleN > 0 {
                Text("·").foregroundStyle(.quaternary)
                // свёрнутые «Старые» не должны терять счёт: чистится экран,
                // а не ответственность (advisory DS r1 по #475)
                Text(L.t("\(staleN) старых", "\(staleN) stale", "\(staleN) 旧"))
                    .foregroundStyle(.tertiary)
            }
            if s.done > 0 {
                Text("·").foregroundStyle(.quaternary)
                Text(L.t("\(s.done) сделано", "\(s.done) done", "\(s.done) 已完成"))
                    .foregroundStyle(.tertiary)
            }
        }
        .font(.caption)
        .fixedSize()
    }

    /// «Мои» — первая секция всегда, даже давние (владелец, 01.09).
    @ViewBuilder private var mineSection: some View {
        let mine = splitOpen.mine
        if !mine.isEmpty {
            Section {
                ForEach(mine) { item in row(item, showSource: true) }
            } header: {
                Text(TasksMineStale.mineTitle)
                    .font(.caption).foregroundStyle(Theme.accent)
            }
        }
    }

    /// «Старые» — свёрнуты (isExpanded явный: гарантия, а не дефолт —
    /// GLM r1). Поиск и фокус-режим встречи складку выключают: совпадение
    /// не должно прятаться, а список конкретной встречи человек открыл
    /// осознанно (GLM Imp-4 + advisory).
    @State private var staleExpanded = false
    @ViewBuilder private var staleSection: some View {
        let stale = splitOpen.stale
        if !stale.isEmpty {
            let unfolded = !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                || navigation.selectedTaskMeetingID != nil
            Section {
                if unfolded {
                    ForEach(stale) { item in row(item, showSource: true) }
                } else {
                    DisclosureGroup(isExpanded: $staleExpanded) {
                        ForEach(stale) { item in row(item, showSource: true) }
                    } label: {
                        Text(TasksMineStale.staleTitle(stale.count)).font(.caption)
                    }
                }
            } header: {
                if unfolded {
                    Text(TasksMineStale.staleTitle(stale.count))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var dueGroups: [(bucket: TasksScreenPolicy.DueBucket, items: [TasksService.Item])] {
        var byBucket: [TasksScreenPolicy.DueBucket: [TasksService.Item]] = [:]
        let split = splitOpen
        let taken = Set(split.mine.map(\.id)).union(split.stale.map(\.id))
        for item in visible where !taken.contains(item.id) {
            byBucket[TasksScreenPolicy.bucket(text: item.text, done: item.done),
                     default: []].append(item)
        }
        return TasksScreenPolicy.DueBucket.allCases.compactMap { bucket in
            guard let items = byBucket[bucket], !items.isEmpty else { return nil }
            return (bucket: bucket, items: items)
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

    /// Открытые, разложенные политикой: mine / живые / старые. Done идут
    /// прежними путями (тумблер «Сделанные» не трогаем).
    private var splitOpen: (mine: [TasksService.Item], fresh: [TasksService.Item],
                            stale: [TasksService.Item]) {
        let open = visibleOpen
        let s = TasksScreenPolicy.split(open.map { ($0.text, $0.happenedAt) },
                                        owner: TasksMineStale.owner)
        return (s.mine.map { open[$0] }, s.fresh.map { open[$0] }, s.stale.map { open[$0] })
    }

    private var groups: [(rel: String, items: [TasksService.Item])] {
        var order: [String] = []
        var byRel: [String: [TasksService.Item]] = [:]
        let split = splitOpen
        let taken = Set(split.mine.map(\.id)).union(split.stale.map(\.id))
        for item in visible where !taken.contains(item.id) {
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

    private func row(_ item: TasksService.Item, showSource: Bool = false) -> some View {
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
            if showSource {
                Text(TasksService.sourceTitle(item.rel))
                    .font(.caption2).foregroundStyle(.tertiary)
                    .lineLimit(1)
                    .layoutPriority(0.5)
            }
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

    private var emptyTitle: String {
        if !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return L.t("Ничего не нашлось", "Nothing found", "未找到")
        }
        if scopedOpenCount == 0 && !scoped.isEmpty {
            return L.t("Всё сделано", "All done", "全部完成")
        }
        return L.t("Поручений пока нет", "No action items yet", "还没有任务")
    }

    private var emptyText: String {
        if !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return L.t("По этому запросу поручений нет — попробуйте другое слово или сбросьте поиск.",
                       "No action items match this search — try another word or clear the search.",
                       "没有符合搜索条件的任务——换个词或清除搜索。")
        }
        if scopedOpenCount == 0 && !scoped.isEmpty {
            return L.t("Открытых поручений не осталось; сделанные — по тумблеру «Сделанные».",
                       "No open action items left; finished ones are behind the “Done” toggle.",
                       "没有未完成任务；已完成的在「已完成」开关后。")
        }
        return L.t("Поручения из минуток появятся здесь после первой встречи. Markdown остаётся источником истины: галочка здесь — галочка в файле.",
                   "Action items from minutes appear here after the first meeting. Markdown remains the source of truth: a tick here is a tick in the file.",
                   "首次会议后，纪要中的任务会显示在这里。Markdown 仍是真实来源：这里打勾即文件中打勾。")
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
