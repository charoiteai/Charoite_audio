import SwiftUI

#if os(macOS)

/// Последние встречи: что записано, что обработалось, что упало.
///
/// До этого списка приложение показывало ровно одну встречу — последнюю.
/// Вчерашняя ошибка исчезала с экрана в ту секунду, когда начиналась новая
/// запись, и найти её можно было только в файлах. Между тем статусы уже
/// лежали на диске две недели: не хватало окна, чтобы на них посмотреть.
struct RecentMeetingsView: View {
    @ObservedObject private var processing = MeetingProcessingService.shared
    /// Встреча, открытая карточкой. Snapshot Identifiable по meetingID —
    /// sheet(item:) сам закрывается и открывается при переключении строк.
    @State private var cardMeeting: MeetingProcessingSnapshot?
    /// Поиск по архиву: окно отвечает не только «что со вчерашней записью»,
    /// но и «где мы решали про X» — по саммари, минуткам и разборам.
    @State private var query = ""
    @State private var results: [MeetingSearch.Hit] = []
    @State private var isSearching = false
    @State private var searchTask: Task<Void, Never>?

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: "clock.arrow.circlepath")
                    .foregroundStyle(Theme.accent)
                Text(L.t("Последние встречи", "Recent meetings", "最近的会议"))
                    .font(.headline).fixedSize()
                Text(L.t("за две недели", "last two weeks", "近两周"))
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                TextField(L.t("Поиск по встречам…", "Search meetings…", "搜索会议…"),
                          text: $query)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 190)
                    .onSubmit { runSearch(debounced: false) }
                    .onChange(of: query) { _, q in
                        if q.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                            cancelSearch()
                        } else {
                            runSearch()
                        }
                    }
                if isSearching {
                    ProgressView().controlSize(.small)
                        .accessibilityLabel(L.t("Ищу встречи", "Searching meetings", "正在搜索会议"))
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            Divider()

            if !query.isEmpty {
                if isSearching && results.isEmpty {
                    VStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text(L.t("Ищу в саммари, минутках и разборах…",
                                 "Searching summaries, minutes and debriefs…",
                                 "正在摘要、纪要和复盘中搜索…"))
                            .font(.subheadline).foregroundStyle(.tertiary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if results.isEmpty {
                    VStack(spacing: 8) {
                        Image(systemName: "magnifyingglass")
                            .font(.largeTitle).foregroundStyle(.quaternary)
                        Text(L.t("Ничего не нашлось в саммари, минутках и разборах.",
                                 "Nothing found in summaries, minutes or debriefs.",
                                 "在摘要、纪要和复盘中未找到任何内容。"))
                            .font(.subheadline).foregroundStyle(.tertiary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    List(results) { hit in
                        Button {
                            NSWorkspace.shared.open(hit.file)
                        } label: {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(hit.title).font(.body.weight(.medium)).lineLimit(1)
                                Text(hit.snippet).font(.caption)
                                    .foregroundStyle(.secondary).lineLimit(2)
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .padding(.vertical, 3)
                    }
                    .listStyle(.inset)
                }
            } else if processing.history.isEmpty {
                VStack(spacing: 10) {
                    Image(systemName: "waveform")
                        .font(.largeTitle).foregroundStyle(.quaternary)
                    Text(L.t("Записанные встречи появятся здесь.\nПервая — сразу после «Слушать встречу».",
                             "Recorded meetings appear here.\nThe first one right after “Listen to meeting”.",
                             "录制的会议会显示在这里。\n第一场就在「聆听会议」之后。"))
                        .font(.subheadline).foregroundStyle(.tertiary)
                        .multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(processing.history, id: \.meetingID) { meeting in
                    row(meeting)
                        .padding(.vertical, 3)
                }
                .listStyle(.inset)
            }
        }
        .frame(minWidth: 460, minHeight: 320)
        .sheet(item: $cardMeeting) { meeting in
            MeetingCardView(meeting: meeting)
        }
        .onDisappear { cancelSearch() }
    }

    /// Поиск начинается после короткой паузы во вводе. Каждый новый символ
    /// отменяет прежнее чтение архива, поэтому медленный старый запрос не
    /// может перезаписать результат более нового.
    private func runSearch(debounced: Bool = true) {
        searchTask?.cancel()
        let submitted = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !submitted.isEmpty, let graph = AppSettings.graphDir else {
            cancelSearch()
            return
        }
        results = []
        isSearching = true
        searchTask = Task { @MainActor in
            if debounced {
                do {
                    try await Task.sleep(nanoseconds: 350_000_000)
                } catch {
                    return
                }
            }
            guard !Task.isCancelled else { return }
            let found = await MeetingSearch.searchAsync(submitted, graph: graph)
            guard !Task.isCancelled,
                  query.trimmingCharacters(in: .whitespacesAndNewlines) == submitted else { return }
            results = found
            isSearching = false
        }
    }

    private func cancelSearch() {
        searchTask?.cancel()
        searchTask = nil
        results = []
        isSearching = false
    }

    @ViewBuilder
    private func row(_ meeting: MeetingProcessingSnapshot) -> some View {
        let state = MeetingProcessingPolicy.resolvedState(meeting)
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Circle().fill(color(state)).frame(width: 7, height: 7)
                if state == .ready {
                    // Готовая встреча открывается карточкой: результат виден в
                    // приложении, а не «идите разбираться с файлом».
                    Button {
                        cardMeeting = meeting
                    } label: {
                        HStack(spacing: 5) {
                            Text(meeting.title)
                                .font(.body.weight(.medium))
                                .lineLimit(1)
                            Image(systemName: "chevron.right")
                                .font(.caption2).foregroundStyle(.tertiary)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .help(L.t("Карточка встречи: суть, решения, поручения",
                              "Meeting card: gist, decisions, action items",
                              "会议卡片：要点、决定、任务"))
                } else {
                    Text(meeting.title)
                        .font(.body.weight(.medium))
                        .lineLimit(1)
                }
                Spacer(minLength: 8)
                // Длительность — только у готовых: пока конвейер работает,
                // таймкоды ещё дописываются и цифра врала бы.
                if state == .ready,
                   let dur = MeetingDurationCache.durationText(for: meeting) {
                    Text(dur + " · ")
                        .font(.caption).foregroundStyle(.tertiary)
                        .fixedSize()
                }
                Text(when(meeting.startedDate))
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize()
            }

            HStack(spacing: 8) {
                Text(status(meeting, state))
                    .font(.caption)
                    .foregroundStyle(state == .error ? Color.red : .secondary)
                    .lineLimit(2)
                Spacer(minLength: 8)
                actions(meeting, state)
            }
        }
    }

    @ViewBuilder
    private func actions(
        _ meeting: MeetingProcessingSnapshot,
        _ state: MeetingProcessingSnapshot.State
    ) -> some View {
        HStack(spacing: 10) {
            if state == .ready, meeting.notePath != nil {
                Button(L.t("Открыть", "Open", "打开")) { processing.open(meeting) }
                    .charoite(.regular, .s)
            }
            if state != .processing {
                Button(L.t("Стенограмма", "Transcript", "逐字稿")) {
                    processing.openTranscript(meeting)
                }
                .charoite(.regular, .s)
            }
            switch MeetingProcessingPolicy.retryControl(
                for: meeting,
                transcriptExists: FileManager.default.fileExists(atPath: meeting.transcriptPath),
                retryingID: processing.retryingID) {
            case .running:
                HStack(spacing: 5) {
                    ProgressView().controlSize(.small)
                    Text(L.t("Повторяю…", "Retrying…", "重试中…"))
                        .font(.caption).foregroundStyle(.secondary)
                }
            case .ready:
                Button(L.t("Повторить", "Retry", "重试")) { processing.retry(meeting) }
                    .charoite(.regular, .s)
            case .waiting:
                Button(L.t("Повторить", "Retry", "重试")) {}
                    .charoite(.regular, .s)
                    .disabled(true)
            case .hidden:
                EmptyView()
            }
        }
        .fixedSize()
    }

    private func color(_ state: MeetingProcessingSnapshot.State) -> Color {
        switch state {
        case .ready: return .green
        case .processing: return .accentColor
        case .error: return .orange
        // Тишина в записи — не авария: серым, чтобы взгляд не цеплялся за
        // строку, с которой всё в порядке.
        case .empty: return .secondary
        case .unknown: return .secondary
        }
    }

    /// Что с встречей — теми же словами, что и в главном окне.
    private func status(
        _ meeting: MeetingProcessingSnapshot,
        _ state: MeetingProcessingSnapshot.State
    ) -> String {
        switch state {
        case .ready:
            return L.t("Готово", "Ready", "已完成")
        case .processing:
            return L.t("Обрабатывается…", "Processing…", "处理中…")
        case .error:
            let head = L.t("Ошибка — исходник сохранён",
                           "Failed — source kept",
                           "失败——原始文件已保留")
            // причина от конвейера: она уже человеческая и здесь важнее
            // единообразия строк
            if let detail = meeting.error, !detail.isEmpty { return head + ". " + detail }
            return head
        case .empty:
            return L.t("Речи нет — запись пустая",
                       "No speech — empty recording",
                       "无语音——录音为空")
        case .unknown:
            return L.t("Статус не распознан", "Unrecognized status", "状态无法识别")
        }
    }

    /// «сегодня 14:20», «вчера 17:40», «29 июля 11:00».
    private func when(_ date: Date) -> String {
        let time = DateFormatter()
        time.locale = Locale.current
        time.setLocalizedDateFormatFromTemplate("HH:mm")
        let clock = time.string(from: date)

        let calendar = Calendar.current
        if calendar.isDateInToday(date) {
            return L.t("сегодня", "today", "今天") + " " + clock
        }
        if calendar.isDateInYesterday(date) {
            return L.t("вчера", "yesterday", "昨天") + " " + clock
        }
        let day = DateFormatter()
        day.locale = Locale.current
        day.setLocalizedDateFormatFromTemplate("d MMMM")
        return day.string(from: date) + " " + clock
    }
}

#endif
