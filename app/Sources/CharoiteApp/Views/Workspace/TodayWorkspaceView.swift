import SwiftUI

#if os(macOS)

/// Домашний экран меняется вместе с жизненным циклом встречи.
struct TodayWorkspaceView: View {
    @ObservedObject private var sufler = SuflerService.shared
    @ObservedObject private var processing = MeetingProcessingService.shared
    @ObservedObject private var repository = MeetingRepository.shared
    @ObservedObject private var calendar = CalendarService.shared
    @ObservedObject private var navigation = WorkspaceNavigation.shared
    @ObservedObject private var tasks = TasksService.shared

    var body: some View {
        VStack(spacing: 0) {
            lifecycle
            Divider()
            HSplitView {
                PrepView()
                    .frame(minWidth: 500, maxWidth: .infinity)
                recent
                    .frame(minWidth: 260, idealWidth: 300, maxWidth: 340)
            }
        }
    }

    private var lifecycle: some View {
        HStack(spacing: 14) {
            Image(systemName: lifecycleIcon)
                .font(.title2).foregroundStyle(Theme.accent)
                .frame(width: 34)
            VStack(alignment: .leading, spacing: 3) {
                Text(lifecycleTitle).font(.headline)
                Text(lifecycleDetail).font(.callout).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer()
            if processing.isProcessing { ProgressView().controlSize(.small) }
            Button(lifecycleActionTitle) { lifecycleAction() }
                .charoite(.prominent)
        }
        .padding(.horizontal, 18).padding(.vertical, 14)
        .background(Theme.accent.opacity(0.055))
    }

    private var recent: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(L.t("Недавние встречи", "Recent meetings", "近期会议"))
                    .font(.headline)
                Spacer()
                Button(L.t("Все", "All", "全部")) { navigation.open(.meetings) }
                    .charoite(.link, .s)
            }
            if repository.records.isEmpty {
                Text(L.t("Здесь появятся три последних результата.",
                         "Your three latest results appear here.",
                         "最近三项结果会显示在这里。"))
                    .font(.callout).foregroundStyle(.secondary)
                Spacer()
            } else {
                ForEach(repository.records.prefix(3)) { record in
                    Button {
                        navigation.open(.meetings, meetingID: record.id)
                    } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(record.title).font(.callout.weight(.medium)).lineLimit(2)
                            Text(record.card.gist ?? compactState(record.state))
                                .font(.caption).foregroundStyle(.secondary).lineLimit(3)
                            let open = tasks.items(for: record.id, includeDone: false).count
                            if open > 0 {
                                Label(L.t("\(open) открытых поручений",
                                          "\(open) open action items",
                                          "\(open) 项未完成任务"),
                                      systemImage: "checklist")
                                    .font(.caption2).foregroundStyle(Theme.accent)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(10)
                        .background(RoundedRectangle(cornerRadius: Theme.radiusCard)
                            .fill(Color(nsColor: .quaternarySystemFill)))
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
                Spacer()
            }
        }
        .padding(14)
    }

    private var lifecycleIcon: String {
        if sufler.isRunning { return "record.circle.fill" }
        if processing.isProcessing { return "gearshape.2" }
        if processing.snapshot?.state == .ready { return "checkmark.circle.fill" }
        return calendar.today.isEmpty ? "sun.max" : "calendar.badge.clock"
    }

    private var lifecycleTitle: String {
        if sufler.isRunning { return L.t("Встреча идёт", "Meeting in progress", "会议进行中") }
        if processing.isProcessing { return L.t("Встреча обрабатывается", "Meeting is processing", "会议处理中") }
        if processing.snapshot?.state == .ready { return L.t("Результат готов", "Result is ready", "结果已就绪") }
        if let event = calendar.today.first { return event.title }
        return L.t("Рабочий день", "Your day", "今日工作")
    }

    private var lifecycleDetail: String {
        if sufler.isRunning { return L.t("Стенограмма, нить и подсказки обновляются в разделе «Встреча».",
                                        "Transcript, thread and hints are updating in Meeting.",
                                        "逐字稿、脉络与提示正在「会议」中更新。") }
        if let status = processing.statusText, processing.isProcessing { return status }
        if let ready = processing.snapshot, ready.state == .ready { return ready.title }
        if let event = calendar.today.first {
            return L.t("Ближайшая встреча · \(Self.time(event.start))",
                       "Next meeting · \(Self.time(event.start))",
                       "下一场会议 · \(Self.time(event.start))")
        }
        return L.t("Новых встреч в календаре сегодня нет.",
                   "No more calendar meetings today.",
                   "今天日历中没有更多会议。")
    }

    private var lifecycleActionTitle: String {
        if sufler.isRunning { return L.t("Открыть встречу", "Open meeting", "打开会议") }
        if processing.isProcessing { return L.t("Показать статус", "Show status", "查看状态") }
        if let ready = processing.snapshot, ready.state == .ready {
            return L.t("Открыть результат", "Open result", "打开结果")
        }
        return L.t("Начать запись", "Start recording", "开始录音")
    }

    private func lifecycleAction() {
        if sufler.isRunning || processing.isProcessing {
            navigation.open(.meeting)
        } else if let ready = processing.snapshot, ready.state == .ready {
            navigation.open(.meetings, meetingID: ready.meetingID)
        } else {
            navigation.open(.meeting)
            sufler.start()
        }
    }

    private func compactState(_ state: MeetingProcessingSnapshot.State) -> String {
        switch state {
        case .ready: return L.t("Готово", "Ready", "已完成")
        case .processing: return L.t("Обрабатывается", "Processing", "处理中")
        case .error: return L.t("Ошибка обработки", "Processing failed", "处理失败")
        case .empty: return L.t("Запись без речи", "Recording without speech", "录音中无语音")
        case .unknown: return L.t("Статус неизвестен", "Unknown status", "状态未知")
        }
    }

    private static func time(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = L.locale
        formatter.setLocalizedDateFormatFromTemplate("HH:mm")
        return formatter.string(from: date)
    }
}

#endif
