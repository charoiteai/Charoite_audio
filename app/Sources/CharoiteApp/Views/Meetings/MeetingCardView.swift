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
    var embedded = false
    @ObservedObject private var processing = MeetingProcessingService.shared
    @ObservedObject private var navigation = WorkspaceNavigation.shared
    @ObservedObject private var tasks = TasksService.shared
    @Environment(\.dismiss) private var dismiss
    @State var card = MeetingCard()
    @State private var renaming = false
    @State private var newTitle = ""
    @State private var renameBusy = false
    @State private var renameFailed = false
    @State private var copied = false
    @State private var actionBusy = false
    @State private var actionMessage = ""
    @State private var forgetPlan = ""
    @State private var showForget = false
    /// Глубина чтения — привычка, а не разовый фильтр: помним между
    /// запусками (сегмент Резюме · Минутки · Разбор · Стенограмма).
    @AppStorage("meetingCardDepth") var depthRaw = MeetingCardDepth.summary.rawValue
    /// Разбор и стенограмма читаются с диска по требованию — строками, чтобы
    /// не резать мегабайтный текст на каждом рендере. Привязаны к встрече:
    /// встроенная карточка живёт одной вью на все встречи, и без
    /// fileMeetingID текст встречи A показывался под заголовком B до конца
    /// чтения (ревью 22.08, DeepSeek — Critical).
    @State var fileLines: [String]?
    @State var fileDepth: MeetingCardDepth?
    @State var fileMeetingID: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    depthPicker
                    if depth == .summary || depth == .minutes {
                        if let gist = card.gist {
                            Text(gist).font(.body)
                        } else if card.summaryMissing {
                            Text(L.t("Саммари ещё не готово — откройте стенограмму.",
                                     "The summary is not ready yet — open the transcript.",
                                     "摘要尚未生成——请打开逐字稿。"))
                                .font(.callout).foregroundStyle(.secondary)
                        }
                    }
                    depthContent
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 2)
            }
            // Откуда это: путь заметки от графа и время последней записи.
            if let source = sourceLine {
                Text(source)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .lineLimit(1).truncationMode(.middle)
                    .textSelection(.enabled)
            }
            if !actionMessage.isEmpty {
                Text(actionMessage)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            Divider()
            actions
        }
        .padding(14)
        .frame(minWidth: 420, minHeight: 400)
        .frame(width: embedded ? nil : 500, height: embedded ? nil : 460)
        // task(id:) — embedded-карточка в библиотеке живёт одной вью на все
        // встречи: без id смена выбора оставляла решения и участников от
        // прошлой встречи под новым заголовком (найдено живым прогоном 04.08).
        .task(id: meeting.meetingID) {
            card = MeetingCardLoader.load(for: meeting)
            tasks.rescan()
        }
        .task(id: depth.rawValue + "|" + meeting.meetingID) { await loadDepthFile() }
        // Привычка «Подробно» из прежнего ключа — в «Минутки», один раз.
        .onAppear { Self.migrateDepthPreference() }
        .sheet(isPresented: $showForget) { forgetSheet }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if renaming {
                    TextField("", text: $newTitle)
                        .textFieldStyle(.roundedBorder)
                        .font(.headline)
                        .onSubmit { runRename() }
                        // Esc — выход без сохранения. Без этого из
                        // переименования не выйти иначе как «Сохранить»,
                        // и случайный клик по карандашу стоил темы.
                        .onExitCommand { renaming = false }
                    Button(L.t("Сохранить", "Save", "保存")) { runRename() }
                        .disabled(renameBusy)
                    Button {
                        renaming = false
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                    // Esc жмёт крестик: onExitCommand на TextField молчит,
                    // пока фокус не в поле, — cancelAction ловит всегда.
                    .keyboardShortcut(.cancelAction)
                    .disabled(renameBusy)
                    .help(L.t("Отменить переименование", "Cancel renaming", "取消重命名"))
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
                if !embedded {
                    Button {
                        dismiss()
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                }
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
                    .foregroundStyle(review.saved ? Color.secondary : Theme.warning)
            }
        }
    }

    @ViewBuilder
    func section(_ title: String, mark: String, items: [String]) -> some View {
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

    @ViewBuilder
    var taskSection: some View {
        let linked = tasks.items(for: meeting.meetingID)
        if linked.isEmpty {
            section(L.t("Поручения", "Action items", "任务"), mark: "▸", items: card.tasks)
        } else {
            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Text(L.t("Поручения", "Action items", "任务"))
                        .font(.subheadline.weight(.semibold))
                    Text(L.t("\(linked.filter { !$0.done }.count) открытых",
                             "\(linked.filter { !$0.done }.count) open",
                             "\(linked.filter { !$0.done }.count) 项未完成"))
                        .font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button(L.t("Все задачи", "All tasks", "全部任务")) {
                        navigation.openTasks(meetingID: meeting.meetingID)
                    }
                    .charoite(.link, .s)
                }
                ForEach(linked) { item in
                    HStack(alignment: .firstTextBaseline, spacing: 7) {
                        Button { tasks.toggle(item) } label: {
                            if tasks.isUpdating(item) {
                                ProgressView().controlSize(.mini).frame(width: 14, height: 14)
                            } else {
                                Image(systemName: item.done ? "checkmark.square.fill" : "square")
                                    .foregroundStyle(item.done ? Theme.accent : Color.secondary)
                            }
                        }
                        .buttonStyle(.plain).disabled(tasks.isUpdating(item))
                        Text(MarkdownLine.render(item.text))
                            .font(.callout)
                            .strikethrough(item.done)
                            .foregroundStyle(item.done ? .secondary : .primary)
                        // Те же чипы срока, что на экране задач: просрочка
                        // видна в карточке, а не только в общем списке.
                        if !item.done, let due = TaskDue.parse(item.text) {
                            Spacer(minLength: 4)
                            DueChip(due: due)
                        }
                    }
                }
                if let error = tasks.mutationError {
                    Text(error).font(.caption).foregroundStyle(Theme.warning)
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
            // Одна дверь во внешний редактор — для той глубины, что открыта.
            // Раньше «Открыть», «Стенограмма» и «Исправить стенограмму…»
            // стояли рядом, и две из них звали одно и то же.
            if let file = depth.file(card: card, meeting: meeting),
               FileManager.default.fileExists(atPath: file.path) {
                Button(L.t("Открыть в редакторе", "Open in editor", "在编辑器中打开")) {
                    NSWorkspace.shared.open(file)
                }
                .charoite(.regular, .s)
                .help(depth == .transcript
                      ? L.t("Исправьте стенограмму и нажмите «Пересобрать результат»",
                            "Fix the transcript, then “Rebuild result”",
                            "修正逐字稿后点按「重建结果」")
                      : file.lastPathComponent)
            }
            Menu(L.t("Ещё", "More", "更多")) {
                if let url = card.obsidianURL {
                    Button("Obsidian") { NSWorkspace.shared.open(url) }
                }
                // «Участникам» обещало доставку; действие — копирование.
                Button(L.t("Скопировать протокол для участников",
                           "Copy the protocol for participants",
                           "复制参会者纪要")) {
                    copyParticipantProtocol()
                }
                Divider()
                Button(L.t("Копировать резюме", "Copy summary", "复制摘要")) {
                    copy(MeetingCardLoader.summaryText(title: meeting.title, card: card))
                }
                Button(L.t("Копировать задачи", "Copy tasks", "复制任务")) {
                    copy(MeetingCardLoader.tasksText(card: card))
                }
                .disabled(card.tasks.isEmpty)
                Button(L.t("Копировать всё", "Copy everything", "复制全部")) {
                    copy(MeetingCardLoader.fullText(
                        title: meeting.title, dateText: dateText, card: card))
                }
                Divider()
                Button(L.t("Пересобрать результат", "Rebuild result", "重建结果")) {
                    processing.rebuild(meeting)
                    actionMessage = L.t("Пересборка запущена",
                                        "Rebuild started",
                                        "已开始重建")
                }
            }
            .menuStyle(.button)
            .charoite(.regular, .s)
            .fixedSize()
            .disabled(actionBusy)
            Spacer()
            if actionBusy { ProgressView().controlSize(.small) }
            if copied {
                Text(L.t("Скопировано", "Copied", "已复制"))
                    .font(.caption).foregroundStyle(.secondary)
                    .transition(.opacity)
            }
            // Удаление — на виду, но тихое: красный текст без заливки.
            // Убранное в меню «Забыть» находили дольше, чем боялись нажать.
            Button(L.t("Забыть встречу…", "Forget meeting…", "忘记会议…")) {
                prepareForget()
            }
            .charoite(.destructive, .s)
            .disabled(actionBusy)
        }
    }

    private var forgetSheet: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(L.t("Забыть встречу", "Forget meeting", "忘记会议"),
                  systemImage: "trash")
                .font(.headline).foregroundStyle(Theme.danger)
            Text(L.t("Будут удалены перечисленные ниже следы. Это действие нельзя отменить.",
                     "The traces listed below will be deleted. This cannot be undone.",
                     "下列痕迹将被删除。此操作无法撤销。"))
                .font(.callout).foregroundStyle(.secondary)
            ScrollView {
                Text(forgetPlan)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(8)
            .background(RoundedRectangle(cornerRadius: Theme.radiusCard)
                .fill(Color(nsColor: .quaternarySystemFill)))
            HStack {
                Spacer()
                // Отмена именно тихая: заметная в диалоге тянет курсор к
                // себе, а Escape и так закрывает.
                Button(L.t("Отмена", "Cancel", "取消")) { showForget = false }
                    .charoite(.quiet, .l)
                    .keyboardShortcut(.cancelAction)
                Button(L.t("Удалить безвозвратно", "Delete permanently", "永久删除"),
                       role: .destructive) { runForget() }
                    .charoite(.destructiveFilled, .l)
                    .disabled(actionBusy)
            }
        }
        .padding(16)
        .frame(width: 600, height: 430)
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

    private func copyParticipantProtocol() {
        guard !actionBusy else { return }
        actionBusy = true
        actionMessage = ""
        Task {
            let result = await MeetingActionsService.participantProtocol(meeting)
            actionBusy = false
            if result.succeeded {
                copy(result.text)
                actionMessage = L.t("Безопасный протокол скопирован — стенограммы в нём нет.",
                                    "Safe protocol copied — it contains no transcript.",
                                    "安全纪要已复制——其中不含逐字稿。")
            } else {
                actionMessage = result.text
            }
        }
    }

    private func prepareForget() {
        guard !actionBusy else { return }
        actionBusy = true
        actionMessage = ""
        Task {
            let result = await MeetingActionsService.forgetPlan(meeting)
            actionBusy = false
            if result.succeeded {
                forgetPlan = result.text
                showForget = true
            } else {
                actionMessage = result.text
            }
        }
    }

    private func runForget() {
        guard !actionBusy else { return }
        actionBusy = true
        Task {
            let result = await MeetingActionsService.forget(meeting)
            actionBusy = false
            if result.succeeded {
                showForget = false
                navigation.selectedMeetingID = nil
                processing.reload()
                tasks.rescan()
                if embedded {
                    navigation.open(.meetings)
                } else {
                    dismiss()
                }
            } else {
                actionMessage = result.text
                showForget = false
            }
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
            if ok {
                processing.reload()
                tasks.rescan()
                if embedded {
                    navigation.selectedMeetingID = nil
                    navigation.open(.meetings)
                } else {
                    dismiss()
                }
            }
        }
    }
}
#endif
