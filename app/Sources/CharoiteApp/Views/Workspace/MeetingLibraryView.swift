import SwiftUI

#if os(macOS)

/// Библиотека встреч в master-detail: список и результат живут рядом.
/// Поисковая находка выбирает ту же карточку, что строка истории.
struct MeetingLibraryView: View {
    @ObservedObject private var repository = MeetingRepository.shared
    @ObservedObject private var navigation = WorkspaceNavigation.shared
    @ObservedObject private var processing = MeetingProcessingService.shared
    @State private var query = ""
    @State private var hits: [MeetingSearch.Hit] = []
    @State private var isSearching = false
    @State private var searchTask: Task<Void, Never>?

    var body: some View {
        HSplitView {
            VStack(spacing: 0) {
                searchBar
                Divider()
                resultList
            }
            .frame(minWidth: 300, idealWidth: 340, maxWidth: 430)

            detail
                .frame(minWidth: 440, maxWidth: .infinity, maxHeight: .infinity)
        }
        .onAppear { chooseDefaultIfNeeded() }
        .onChange(of: repository.records) { _, _ in chooseDefaultIfNeeded() }
        .onDisappear { cancelSearch() }
    }

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

    @ViewBuilder
    private var resultList: some View {
        if query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            if repository.records.isEmpty {
                emptyList
            } else {
                List(repository.records, selection: $navigation.selectedMeetingID) { record in
                    recordRow(record).tag(record.id)
                }
                .listStyle(.sidebar)
            }
        } else if hits.isEmpty {
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

    private func recordRow(_ record: MeetingRecord) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 7) {
                Circle().fill(stateColor(record.state)).frame(width: 7, height: 7)
                Text(record.title).font(.callout.weight(.medium)).lineLimit(1)
                Spacer()
                Text(relative(record.startedAt)).font(.caption2).foregroundStyle(.tertiary)
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
                if processing.canRetry(record.snapshot) {
                    Button(L.t("Повторить обработку", "Retry processing", "重试处理")) {
                        processing.retry(record.snapshot)
                    }
                    .buttonStyle(.borderedProminent).tint(Theme.accent)
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
            let found = await MeetingSearch.searchAsync(submitted, graph: graph)
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
}

#endif
