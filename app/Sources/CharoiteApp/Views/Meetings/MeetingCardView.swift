import SwiftUI

#if os(macOS)

/// Карточка готовой встречи.
///
/// «Встреча готова» приложение говорило и раньше — а дальше отправляло человека
/// в markdown-файл. Здесь результат виден на месте: суть, решения, поручения,
/// участники и длительность; копирование в письмо одним нажатием; файлы — на
/// расстоянии кнопки; переименование — без обхода пяти мест руками.
struct MeetingCardView: View {
    let meeting: MeetingProcessingSnapshot
    @ObservedObject private var processing = MeetingProcessingService.shared
    @Environment(\.dismiss) private var dismiss
    @State private var card = MeetingCard()
    @State private var renaming = false
    @State private var newTitle = ""
    @State private var renameBusy = false
    @State private var renameFailed = false
    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if let gist = card.gist {
                        Text(gist).font(.body)
                    } else if card.summaryMissing {
                        Text(L.t("Саммари ещё не готово — открой стенограмму.",
                                 "The summary is not ready yet — open the transcript.",
                                 "摘要尚未生成——请打开逐字稿。"))
                            .font(.callout).foregroundStyle(.secondary)
                    }
                    section(L.t("Решили", "Decided", "决定"), mark: "⚑",
                            items: card.decisions)
                    section(L.t("Поручения", "Action items", "任务"), mark: "▸",
                            items: card.tasks)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 2)
            }
            Divider()
            actions
        }
        .padding(14)
        .frame(width: 500, height: 460)
        .task { card = MeetingCardLoader.load(for: meeting) }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if renaming {
                    TextField("", text: $newTitle)
                        .textFieldStyle(.roundedBorder)
                        .font(.headline)
                        .onSubmit { runRename() }
                    Button(L.t("Сохранить", "Save", "保存")) { runRename() }
                        .disabled(renameBusy)
                    if renameBusy { ProgressView().controlSize(.small) }
                } else {
                    Text(meeting.title).font(.headline)
                    Button {
                        newTitle = meeting.title
                        renaming = true
                    } label: {
                        Image(systemName: "pencil")
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                    .help(L.t("Переименовать встречу — тема поменяется во всех файлах и в графе",
                              "Rename the meeting — the topic changes in every file and in the graph",
                              "重命名会议——主题将在所有文件和图谱中更新"))
                }
                Spacer()
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
            }
            HStack(spacing: 6) {
                Text(dateText)
                if let duration = card.durationText {
                    Text("·"); Text(duration)
                }
                if renameFailed {
                    Text(L.t("— переименовать не вышло, см. логи",
                             "— rename failed, see logs",
                             "— 重命名失败，请查看日志"))
                        .foregroundStyle(.red)
                }
            }
            .font(.caption).foregroundStyle(.secondary)
            if !card.participants.isEmpty {
                Text(L.t("Участники: ", "Participants: ", "参会者：")
                     + card.participants.joined(separator: ", "))
                    .font(.caption).foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            // Облачная ревизия работала невидимкой: правки графа делались,
            // а человек узнавал о них только из лога. Одна честная строка.
            if let review = card.cloudReview {
                Label(review.saved
                      ? L.t("Ревизия Claude: правок графа — \(review.edits)",
                            "Claude review: \(review.edits) graph edits",
                            "Claude 复审：图谱修改 \(review.edits) 处")
                      : L.t("Ревизия Claude: правок графа — \(review.edits), файл ревизии не сохранился",
                            "Claude review: \(review.edits) graph edits, the review file was not saved",
                            "Claude 复审：图谱修改 \(review.edits) 处，复审文件未保存"),
                      systemImage: "cloud")
                    .font(.caption)
                    .foregroundStyle(review.saved ? Color.secondary : .orange)
            }
        }
    }

    @ViewBuilder
    private func section(_ title: String, mark: String, items: [String]) -> some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.subheadline.weight(.semibold))
                ForEach(items, id: \.self) { item in
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(mark).foregroundStyle(Theme.accent)
                        Text(cleanItem(item))
                    }
                    .font(.callout)
                }
            }
        }
    }

    /// Markdown-жир в пунктах саммари: в карточке он лишний.
    private func cleanItem(_ item: String) -> String {
        item.replacingOccurrences(of: "**", with: "")
    }

    private var actions: some View {
        HStack(spacing: 10) {
            if meeting.notePath != nil {
                Button(L.t("Открыть", "Open", "打开")) { processing.open(meeting) }
            }
            Button(L.t("Стенограмма", "Transcript", "逐字稿")) {
                processing.openTranscript(meeting)
            }
            if let url = card.obsidianURL {
                Button("Obsidian") { NSWorkspace.shared.open(url) }
            }
            Spacer()
            if copied {
                Text(L.t("Скопировано", "Copied", "已复制"))
                    .font(.caption).foregroundStyle(.secondary)
                    .transition(.opacity)
            }
            Menu(L.t("Копировать", "Copy", "复制")) {
                Button(L.t("Резюме", "Summary", "摘要")) {
                    copy(MeetingCardLoader.summaryText(title: meeting.title, card: card))
                }
                Button(L.t("Задачи", "Tasks", "任务")) {
                    copy(MeetingCardLoader.tasksText(card: card))
                }
                .disabled(card.tasks.isEmpty)
                Button(L.t("Всё", "Everything", "全部")) {
                    copy(MeetingCardLoader.fullText(
                        title: meeting.title, dateText: dateText, card: card))
                }
            }
            .fixedSize()
        }
        .buttonStyle(.link)
        .font(.callout)
    }

    private var dateText: String {
        let f = DateFormatter()
        f.locale = L.locale
        f.setLocalizedDateFormatFromTemplate("d MMMM HH:mm")
        return f.string(from: meeting.startedDate)
    }

    private func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        withAnimation { copied = true }
        Task {
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            withAnimation { copied = false }
        }
    }

    private func runRename() {
        guard !renameBusy else { return }
        renameBusy = true
        renameFailed = false
        Task {
            let ok = await processing.rename(meeting, to: newTitle)
            renameBusy = false
            renaming = false
            renameFailed = !ok
            // Список обновится сам через refresh(); карточка закрывается,
            // потому что её snapshot держит старый путь и заголовок.
            if ok { dismiss() }
        }
    }
}
#endif
