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
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            Divider()

            if processing.history.isEmpty {
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
    }

    @ViewBuilder
    private func row(_ meeting: MeetingProcessingSnapshot) -> some View {
        let state = MeetingProcessingPolicy.resolvedState(meeting)
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Circle().fill(color(state)).frame(width: 7, height: 7)
                Text(meeting.title)
                    .font(.body.weight(.medium))
                    .lineLimit(1)
                Spacer(minLength: 8)
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
            }
            if state != .processing {
                Button(L.t("Стенограмма", "Transcript", "逐字稿")) {
                    processing.openTranscript(meeting)
                }
            }
            if processing.canRetry(meeting) || processing.retryInFlight {
                Button(L.t("Повторить", "Retry", "重试")) { processing.retry(meeting) }
                    .disabled(!processing.canRetry(meeting))
            }
        }
        .buttonStyle(.link)
        .font(.caption)
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
