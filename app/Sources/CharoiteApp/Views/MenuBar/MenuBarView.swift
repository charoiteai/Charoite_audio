import SwiftUI

#if os(macOS)

/// Меню-бар: статус, быстрый вопрос локальной модели, диктовка и заметка.
struct MenuBarView: View {
    @ObservedObject private var sufler = SuflerService.shared
    @ObservedObject private var processing = MeetingProcessingService.shared
    @ObservedObject private var dictation = DictationService.shared
    @ObservedObject private var chat = LocalChatService.shared
    @Environment(\.openWindow) private var openWindow
    @State private var quick = ""
    @State private var stackNote = ""   // здоровье стека: пусто = всё в порядке

    /// Что происходит прямо сейчас — одной строкой и одним цветом.
    ///
    /// Приложение живёт в меню-баре, и окно после встречи обычно закрывают.
    /// Раньше здесь были только «Идёт запись» и «Готов»: всё, что случалось
    /// с встречей после «Стоп» — обработка, готовый результат, ошибка, —
    /// было видно только в окне, то есть чаще всего нигде.
    private var state: (text: String, color: Color) {
        if sufler.isRunning {
            return (L.t("Запись", "Recording", "录音中") + " · "
                    + SuflerService.clockText(sufler.recordingElapsed), .red)
        }
        if processing.isError {
            return (L.t("Ошибка — исходник сохранён",
                        "Failed — source kept",
                        "处理失败——原始文件已保留"), .orange)
        }
        if processing.isProcessing {
            return (L.t("Обрабатываю встречу…", "Processing…", "正在处理…"), .accentColor)
        }
        if processing.actionTitle != nil {
            return (L.t("Встреча готова", "Meeting ready", "会议已就绪"), .green)
        }
        if !stackNote.isEmpty { return (stackNote, .orange) }
        return (L.t("Готов к записи", "Ready to record", "可以录音"), .green)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(L.t("Чароит", "Charoite", "Charoite")).font(.headline)
                Spacer()
                Circle()
                    .fill(state.color)
                    .frame(width: 8, height: 8)
                Text(state.text)
                    .font(.caption).foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            // здоровье стека проверяется при открытии меню: молча зелёный,
            // а если Ollama лежит — видно ДО того, как вопрос уйдёт в пустоту
            .task { await checkStack() }

            // Запись и результат — прямо здесь: за ними не нужно открывать окно.
            HStack(spacing: 10) {
                if sufler.isRunning {
                    Button {
                        SuflerService.shared.stop()
                    } label: {
                        Label(L.t("Остановить", "Stop", "停止"), systemImage: "stop.circle")
                    }
                } else {
                    Button {
                        SuflerService.shared.start()
                    } label: {
                        Label(L.t("Начать запись", "Start recording", "开始录音"),
                              systemImage: "record.circle")
                    }
                    .disabled(processing.isProcessing)
                }
                if !sufler.isRunning, let title = processing.actionTitle {
                    Button(title) { processing.openResult() }
                }
                if !sufler.isRunning, processing.canRetry || processing.retryInFlight {
                    Button(L.t("Повторить", "Retry", "重试")) { processing.retry() }
                        .disabled(processing.retryInFlight)
                }
                if !processing.history.isEmpty {
                    Button(L.t("Все встречи", "All meetings", "全部会议")) {
                        openWindow(id: "meetings")
                        NSApp.activate(ignoringOtherApps: true)
                    }
                }
            }
            .buttonStyle(.plain)
            .font(.caption)

            HStack(spacing: 8) {
                TextField(L.t("Быстрый вопрос…", "Quick question…", "快速提问…"), text: $quick)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit(sendQuick)
                Button {
                    sendQuick()
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                }
                .buttonStyle(.plain)
                .disabled(quick.trimmingCharacters(in: .whitespaces).isEmpty)
            }

            HStack(spacing: 14) {
                Button {
                    DictationService.shared.toggle()
                } label: {
                    Label(L.t("Диктовка ⌥⌘D", "Dictation ⌥⌘D", "听写 ⌥⌘D"), systemImage: dictation.isRecording ? "mic.fill" : "mic")
                }
                Button {
                    DictationService.shared.toggleNote()
                } label: {
                    Label(L.t("Заметка ⌥⌘N", "Voice note ⌥⌘N", "语音笔记 ⌥⌘N"), systemImage: "note.text.badge.plus")
                }
                Button {
                    DictationService.shared.toggleDiary()
                } label: {
                    Label(L.t("Дневник ⌥⌘J", "Diary ⌥⌘J", "日记 ⌥⌘J"), systemImage: "book.closed")
                }
            }
            .buttonStyle(.plain)
            .font(.caption)

            if !dictation.status.isEmpty {
                Text(dictation.status).font(.caption2).foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            Divider()

            HStack {
                Button(L.t("Открыть", "Open", "打开")) {
                    NSApp.activate(ignoringOtherApps: true)
                    NSApp.windows.first { $0.canBecomeMain }?.makeKeyAndOrderFront(nil)
                }
                Spacer()
                Button {
                    NSApp.terminate(nil)
                } label: {
                    Label(L.t("Выход", "Quit", "退出"), systemImage: "power")
                }
            }
            .buttonStyle(.plain)
            .font(.callout)
        }
        .padding(14)
        .frame(width: 300)
    }

    /// Ollama доступна? Одна лёгкая проверка при открытии меню.
    private func checkStack() async {
        guard let url = URL(string: AppSettings.ollamaURL + "/api/tags") else { return }
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]
        cfg.timeoutIntervalForRequest = 2
        let ok = (try? await URLSession(configuration: cfg).data(from: url)) != nil
        stackNote = ok ? "" : "Ollama не отвечает"
    }

    /// Быстрый вопрос уходит в общий локальный чат — ответ ждёт в его истории.
    private func sendQuick() {
        let q = quick.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return }
        quick = ""
        chat.send(q)
        openWindow(id: "localchat")
        NSApp.activate(ignoringOtherApps: true)
    }
}

#endif
