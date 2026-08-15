import SwiftUI

#if os(macOS)

/// Библиотека встреч в master-detail: список и результат живут рядом.
/// Поисковая находка выбирает ту же карточку, что строка истории.
///
/// Сверху — полоса недели: точки на днях с записями, клик по дню
/// превращает список в ленту дня (записи + события календаря без записи).
/// Календарь здесь не отдельный экран, а навигация по архиву: вопрос
/// «что было во вторник?» решается одним кликом, а не поиском.
struct MeetingLibraryView: View {
    @ObservedObject private var repository = MeetingRepository.shared
    @ObservedObject private var navigation = WorkspaceNavigation.shared
    @ObservedObject private var processing = MeetingProcessingService.shared
    @ObservedObject private var calendar = CalendarService.shared
    @AppStorage("charoite.calendarBriefs") private var calendarBriefs = false
    @State private var query = ""
    @State private var hits: [MeetingSearch.Hit] = []
    @State private var isSearching = false
    @State private var searchTask: Task<Void, Never>?
    /// nil — весь архив; дата — лента одного дня.
    @State private var selectedDay: Date?
    @State private var weekAnchor = Calendar.current.startOfDay(for: Date())
    @State private var dayEvents: [CalendarService.DayEvent] = []

    var body: some View {
        HSplitView {
            VStack(spacing: 0) {
                weekStrip
                Divider()
                searchBar
                Divider()
                resultList
            }
            .frame(minWidth: 300, idealWidth: 340, maxWidth: 430)

            detail
                .frame(minWidth: 440, maxWidth: .infinity, maxHeight: .infinity)
        }
        .onAppear {
            chooseDefaultIfNeeded()
            reloadDayEvents()
        }
        .onChange(of: repository.records) { _, _ in chooseDefaultIfNeeded() }
        .onChange(of: selectedDay) { _, _ in reloadDayEvents() }
        .onChange(of: calendar.accessGranted) { _, _ in reloadDayEvents() }
        .onChange(of: calendar.eventsRevision) { _, _ in reloadDayEvents() }
        .onDisappear { cancelSearch() }
    }

    // MARK: - Полоса недели

    private var searchActive: Bool {
        !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var weekDays: [Date] {
        let cal = Calendar.current
        let start = cal.dateInterval(of: .weekOfYear, for: weekAnchor)?.start ?? weekAnchor
        return (0..<7).compactMap { cal.date(byAdding: .day, value: $0, to: start) }
    }

    private var recordedDays: Set<Date> {
        Set(repository.records.map { Calendar.current.startOfDay(for: $0.startedAt) })
    }

    private var weekStrip: some View {
        HStack(spacing: 3) {
            Button { shiftWeek(-1) } label: { Image(systemName: "chevron.left") }
                .charoite(.icon, .s)
                .help(L.t("Прошлая неделя", "Previous week", "上一周"))
                .accessibilityLabel(Text(L.t("Прошлая неделя", "Previous week", "上一周")))
            ForEach(weekDays, id: \.self) { day in
                dayCell(day)
            }
            Button { shiftWeek(1) } label: { Image(systemName: "chevron.right") }
                .charoite(.icon, .s)
                .help(L.t("Следующая неделя", "Next week", "下一周"))
                .accessibilityLabel(Text(L.t("Следующая неделя", "Next week", "下一周")))
            Spacer(minLength: 4)
            if calendar.accessGranted != true {
                Button {
                    calendarBriefs = true
                    calendar.enable(askForNotifications: true)
                } label: {
                    Image(systemName: "calendar.badge.plus")
                }
                .charoite(.icon, .s)
                .help(L.t("Подключить календарь: события дня появятся рядом с записями (локально, только чтение)",
                          "Connect the calendar: the day's events appear next to recordings (local, read-only)",
                          "连接日历：当天日程将与录音并排显示（本地，只读）"))
                .accessibilityLabel(Text(L.t("Подключить календарь", "Connect calendar", "连接日历")))
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .opacity(searchActive ? 0.4 : 1)
        .allowsHitTesting(!searchActive)
    }

    private func dayCell(_ day: Date) -> some View {
        let cal = Calendar.current
        let isSelected = selectedDay.map { cal.isDate(day, inSameDayAs: $0) } ?? false
        let isToday = cal.isDate(day, inSameDayAs: Date())
        let hasRecords = recordedDays.contains(cal.startOfDay(for: day))
        return Button {
            selectedDay = isSelected ? nil : cal.startOfDay(for: day)
        } label: {
            VStack(spacing: 2) {
                Text(Self.weekdayFormatter.string(from: day))
                    .font(.caption2)
                    .foregroundStyle(isSelected ? Color.white.opacity(0.85) : .secondary)
                Text(Self.dayNumberFormatter.string(from: day))
                    .font(.callout.weight(isToday ? .bold : .regular).monospacedDigit())
                    .foregroundStyle(isSelected ? .white : (isToday ? Theme.accent : .primary))
                Circle()
                    .fill(hasRecords ? (isSelected ? Color.white : Theme.accent) : Color.clear)
                    .frame(width: 4, height: 4)
            }
            .frame(width: 34)
            .padding(.vertical, 4)
            .background(RoundedRectangle(cornerRadius: 7)
                .fill(isSelected ? Theme.accent : Color.clear))
            .contentShape(RoundedRectangle(cornerRadius: 7))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(Text(Self.fullDayFormatter.string(from: day)))
    }

    private func shiftWeek(_ direction: Int) {
        if let day = Calendar.current.date(byAdding: .day, value: direction * 7, to: weekAnchor) {
            weekAnchor = Calendar.current.startOfDay(for: day)
        }
    }

    private func reloadDayEvents() {
        guard let day = selectedDay else {
            dayEvents = []
            return
        }
        dayEvents = calendar.events(on: day)
    }

    // MARK: - Поиск

    private var searchBar: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
            TextField(L.t("Тема, участник, решение…",
                          "Topic, participant, decision…",
                          "主题、参会者、决定…"), text: $query)
                .textFieldStyle(.plain)
                .onSubmit { runSearch(debounced: false) }
                .onChange(of: query) { _, value in
                    value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        ? cancelSearch() : runSearch()
                }
            if isSearching { ProgressView().controlSize(.small) }
            if !query.isEmpty {
                Button {
                    query = ""
                    cancelSearch()
                } label: { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.plain).foregroundStyle(.secondary)
            }
        }
        .padding(11)
    }

    // MARK: - Список

    @ViewBuilder
    private var resultList: some View {
        if searchActive {
            searchResults
        } else if let day = selectedDay {
            dayFeed(day)
        } else if repository.records.isEmpty {
            emptyList
        } else {
            // selection: String? — тег тоже обязан быть Optional, иначе
            // строка не выбирается кликом (та же ловушка, что в сайдбаре).
            List(selection: $navigation.selectedMeetingID) {
                ForEach(repository.records) { record in
                    recordRow(record).tag(record.id as String?)
                }
            }
            .listStyle(.sidebar)
        }
    }

    @ViewBuilder
    private var searchResults: some View {
        if hits.isEmpty {
            VStack(spacing: 9) {
                if isSearching { ProgressView().controlSize(.small) }
                Image(systemName: "magnifyingglass").foregroundStyle(.quaternary)
                Text(isSearching
                     ? L.t("Ищу в материалах встреч…", "Searching meeting materials…", "正在搜索会议资料…")
                     : L.t("Ничего не найдено", "Nothing found", "未找到结果"))
                    .font(.callout).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            List(hits) { hit in
                Button { open(hit) } label: {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(hit.title).font(.callout.weight(.medium)).lineLimit(1)
                        Text(hit.snippet).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .padding(.vertical, 3)
            }
            .listStyle(.sidebar)
        }
    }

    // MARK: - Лента дня

    private func dayRecords(_ day: Date) -> [MeetingRecord] {
        repository.records
            .filter { Calendar.current.isDate($0.startedAt, inSameDayAs: day) }
            .sorted { $0.startedAt < $1.startedAt }
    }

    /// События, к которым не прикрепилась ни одна запись: их показываем
    /// приглушёнными строками. События с записью отдельно не показываем —
    /// запись сама говорит за встречу, дубль только путал бы.
    private func unrecordedEvents(_ day: Date) -> [CalendarService.DayEvent] {
        let board = CalendarDayMatch.board(
            events: dayEvents,
            recordStarts: dayRecords(day).map { ($0.id, $0.startedAt) })
        return board.slots.filter { $0.recordIDs.isEmpty }.map { $0.event }
    }

    @ViewBuilder
    private func dayFeed(_ day: Date) -> some View {
        let records = dayRecords(day)
        let missed = unrecordedEvents(day)
        VStack(spacing: 0) {
            HStack(spacing: 6) {
                Text(Self.fullDayFormatter.string(from: day))
                    .font(.callout.weight(.semibold))
                Text(dayCount(records: records.count, events: missed.count))
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button {
                    selectedDay = nil
                } label: { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.plain).foregroundStyle(.secondary)
                    .help(L.t("Весь архив", "Whole archive", "全部档案"))
                    .accessibilityLabel(Text(L.t("Весь архив", "Whole archive", "全部档案")))
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            Divider()
            switch CalendarDayMatch.emptyState(
                recordCount: records.count,
                eventCount: missed.count,
                calendarConnected: calendar.accessGranted == true
            ) {
            case .calendarUnavailable:
                calendarUnavailableDay
            case .quietDay:
                VStack(spacing: 8) {
                    Image(systemName: "moon.zzz").foregroundStyle(.quaternary)
                    Text(L.t("Тихий день: ни записей, ни событий.",
                             "A quiet day: no recordings, no events.",
                             "安静的一天：没有录音，也没有日程。"))
                        .font(.callout).foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .none:
                List(selection: $navigation.selectedMeetingID) {
                    ForEach(records) { record in
                        recordRow(record, timeOfDay: true).tag(record.id as String?)
                    }
                    ForEach(missed) { event in
                        eventRow(event)
                    }
                }
                .listStyle(.sidebar)
            }
        }
    }

    private var calendarUnavailableDay: some View {
        VStack(spacing: 8) {
            Image(systemName: "calendar.badge.exclamationmark")
                .foregroundStyle(.quaternary)
            Text(L.t("Записей за этот день нет. События календаря недоступны.",
                     "No recordings for this day. Calendar events are unavailable.",
                     "这一天没有录音，且无法读取日历事件。"))
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button(L.t("Подключить календарь", "Connect calendar", "连接日历")) {
                calendarBriefs = true
                calendar.enable(askForNotifications: true)
            }
            .charoite(.prominent)
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func dayCount(records: Int, events: Int) -> String {
        var parts: [String] = []
        if records > 0 {
            parts.append(L.t("записей: \(records)", "\(records) recordings", "录音 \(records)"))
        }
        if events > 0 {
            parts.append(L.t("без записи: \(events)", "\(events) not recorded", "未录音 \(events)"))
        }
        return parts.joined(separator: " · ")
    }

    /// Событие календаря без записи: время и правда о нём — прошло мимо
    /// архива, идёт сейчас или ещё впереди. Не кликается: открывать нечего.
    private func eventRow(_ event: CalendarService.DayEvent) -> some View {
        let now = Date()
        let end = event.end ?? event.start.addingTimeInterval(30 * 60)
        let isLive = event.start <= now && now <= end
        let isFuture = event.start > now
        return HStack(spacing: 7) {
            Text(Self.timeFormatter.string(from: event.start))
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
            Text(event.title)
                .font(.callout)
                .lineLimit(1)
                .foregroundStyle(.secondary)
            Spacer()
            if isLive {
                Text(L.t("идёт", "now", "进行中"))
                    .font(.caption.weight(.medium))
                    .foregroundStyle(Theme.accent)
            } else if isFuture {
                Text(L.t("впереди", "upcoming", "即将开始"))
                    .font(.caption2).foregroundStyle(.tertiary)
            } else {
                Text(L.t("без записи", "not recorded", "未录音"))
                    .font(.caption2).foregroundStyle(.tertiary)
            }
        }
        .padding(.vertical, 3)
        .opacity(isFuture || isLive ? 1 : 0.62)
        .selectionDisabled()
    }

    private var emptyList: some View {
        VStack(spacing: 10) {
            Image(systemName: "rectangle.stack").font(.largeTitle).foregroundStyle(.quaternary)
            Text(L.t("После первой записи здесь появится история встреч.",
                     "Your meeting history appears here after the first recording.",
                     "首次录音后，会议历史会显示在这里。"))
                .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @ViewBuilder
    private var detail: some View {
        if let record = repository.record(id: navigation.selectedMeetingID) {
            if record.state == .ready {
                MeetingCardView(meeting: record.snapshot, embedded: true)
            } else {
                processingDetail(record)
            }
        } else {
            ContentUnavailableView(
                L.t("Выберите встречу", "Select a meeting", "请选择会议"),
                systemImage: "rectangle.stack",
                description: Text(L.t("Слева — записи и поиск по решениям.",
                                      "Recordings and decision search are on the left.",
                                      "左侧可查看录音并搜索决定。")))
        }
    }

    private func recordRow(_ record: MeetingRecord, timeOfDay: Bool = false) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 7) {
                Circle().fill(stateColor(record.state)).frame(width: 7, height: 7)
                Text(record.title).font(.callout.weight(.medium)).lineLimit(1)
                Spacer()
                Text(timeOfDay
                     ? Self.timeFormatter.string(from: record.startedAt)
                     : relative(record.startedAt))
                    .font(.caption2.monospacedDigit()).foregroundStyle(.tertiary)
            }
            if let gist = record.card.gist {
                Text(gist).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            } else {
                Text(stateText(record.state)).font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 3)
    }

    private func processingDetail(_ record: MeetingRecord) -> some View {
        VStack(spacing: 14) {
            Image(systemName: record.state == .error ? "exclamationmark.triangle" : "gearshape.2")
                .font(.largeTitle).foregroundStyle(stateColor(record.state))
            Text(record.title).font(.title3.weight(.semibold))
            Text(stateText(record.state)).foregroundStyle(.secondary)
            HStack {
                Button(L.t("Стенограмма", "Transcript", "逐字稿")) {
                    processing.openTranscript(record.snapshot)
                }
                .charoite()
                if processing.canRetry(record.snapshot) {
                    Button(L.t("Повторить обработку", "Retry processing", "重试处理")) {
                        processing.retry(record.snapshot)
                    }
                    .charoite(.prominent)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func runSearch(debounced: Bool = true) {
        searchTask?.cancel()
        let submitted = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !submitted.isEmpty, let graph = AppSettings.graphDir else {
            cancelSearch()
            return
        }
        hits = []
        isSearching = true
        searchTask = Task { @MainActor in
            if debounced {
                do { try await Task.sleep(nanoseconds: 350_000_000) } catch { return }
            }
            // окно библиотеки ищет и по узлам графа: запрос «имя человека»
            // ведёт к его узлу со всей историей, а не только к встречам
            let found = await MeetingSearch.searchAsync(submitted, graph: graph,
                                                        includeNodes: true)
            guard !Task.isCancelled,
                  submitted == query.trimmingCharacters(in: .whitespacesAndNewlines) else { return }
            hits = found
            isSearching = false
        }
    }

    private func cancelSearch() {
        searchTask?.cancel()
        searchTask = nil
        hits = []
        isSearching = false
    }

    private func open(_ hit: MeetingSearch.Hit) {
        // узел графа не встреча: пустой day не должен даже теоретически
        // сматчить карточку записи (ревью 15.08) — сразу файлом
        if hit.kind == .node {
            NSWorkspace.shared.open(hit.file)
            return
        }
        if let record = repository.record(matching: hit) {
            navigation.selectedMeetingID = record.id
        } else {
            NSWorkspace.shared.open(hit.file)
        }
    }

    private func chooseDefaultIfNeeded() {
        guard repository.record(id: navigation.selectedMeetingID) == nil else { return }
        navigation.selectedMeetingID = repository.records.first?.id
    }

    private func stateColor(_ state: MeetingProcessingSnapshot.State) -> Color {
        switch state {
        case .ready: return .green
        case .processing: return Theme.accent
        case .error: return .orange
        case .empty, .unknown: return .secondary
        }
    }

    private func stateText(_ state: MeetingProcessingSnapshot.State) -> String {
        switch state {
        case .ready: return L.t("Готово", "Ready", "已完成")
        case .processing: return L.t("Обрабатывается…", "Processing…", "处理中…")
        case .error: return L.t("Ошибка — исходник сохранён", "Failed — source kept", "失败——原始文件已保留")
        case .empty: return L.t("В записи нет речи", "No speech in the recording", "录音中没有语音")
        case .unknown: return L.t("Неизвестное состояние", "Unknown state", "未知状态")
        }
    }

    private func relative(_ date: Date) -> String {
        let formatter = RelativeDateTimeFormatter()
        formatter.locale = L.locale
        formatter.unitsStyle = .short
        return formatter.localizedString(for: date, relativeTo: Date())
    }

    // MARK: - Форматтеры

    private static let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = .current
        f.dateFormat = "HH:mm"
        return f
    }()

    private static let weekdayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = .current
        f.setLocalizedDateFormatFromTemplate("EE")
        return f
    }()

    private static let dayNumberFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = .current
        f.dateFormat = "d"
        return f
    }()

    private static let fullDayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = .current
        f.setLocalizedDateFormatFromTemplate("EEEE d MMMM")
        return f
    }()
}

#endif
