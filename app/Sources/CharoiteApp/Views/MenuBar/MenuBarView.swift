import SwiftUI

#if os(macOS)

/// Меню-бар: статус, быстрый вопрос локальной модели, диктовка и заметка.
struct MenuBarView: View {
    @ObservedObject private var sufler = SuflerService.shared
    @ObservedObject private var processing = MeetingProcessingService.shared
    @ObservedObject private var dictation = DictationService.shared
    @ObservedObject private var chat = LocalChatService.shared
    @ObservedObject private var navigation = WorkspaceNavigation.shared
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
                        navigation.open(.meeting)
                        SuflerService.shared.start()
                    } label: {
                        Label(L.t("Начать запись", "Start recording", "开始录音"),
                              systemImage: "record.circle")
                    }
                    .disabled(processing.isProcessing)
                }
                if !sufler.isRunning, let title = processing.actionTitle {
                    Button(title) {
                        navigation.open(.meetings, meetingID: processing.snapshot?.meetingID)
                    }
                }
                if !sufler.isRunning, processing.canRetry || processing.retryInFlight {
                    Button(L.t("Повторить", "Retry", "重试")) { processing.retry() }
                        .disabled(processing.retryInFlight)
                }
                // Всегда, а не только при непустой истории: окно честно
                // объясняет пустоту само, а спрятанная кнопка выглядела как
                // отсутствие функции. И «Последние», а не «Все»: список
                // показывает двадцать встреч за две недели, «Все встречи»
                // обещали архив, которого за этой кнопкой нет.
                Button(L.t("Последние встречи", "Recent meetings", "近期会议")) {
                    navigation.open(.meetings)
                }
                Button(L.t("Сегодня", "Today", "今天")) {
                    navigation.open(.today)
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

            // Столбиком, а не в ряд: три подписи с сочетаниями не влезали в
            // 300 точек ширины и обрезались до «Диктовка ⌥…» — то есть именно
            // та часть, ради которой подпись и написана, пропадала. Сочетание
            // теперь стоит справа отдельной колонкой, как в системных меню.
            VStack(spacing: 6) {
                shortcutRow(L.t("Диктовка", "Dictation", "听写"), "⌥⌘D",
                            icon: dictation.isRecording ? "mic.fill" : "mic") {
                    DictationService.shared.toggle()
                }
                shortcutRow(L.t("Заметка", "Voice note", "语音笔记"), "⌥⌘N",
                            icon: "note.text.badge.plus") {
                    DictationService.shared.toggleNote()
                }
                shortcutRow(L.t("Дневник", "Diary", "日记"), "⌥⌘J",
                            icon: "book.closed") {
                    DictationService.shared.toggleDiary()
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
                    navigation.open(.today)
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

    /// Строка действия с сочетанием клавиш справа.
    ///
    /// Сочетание — не украшение подписи: пока оно стояло внутри текста, при
    /// нехватке ширины система резала именно его.
    private func shortcutRow(_ title: String, _ key: String, icon: String,
                             action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Label(title, systemImage: icon)
                Spacer(minLength: 8)
                Text(key)
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
                    .layoutPriority(1)     // подпись ужимается раньше сочетания
            }
            .contentShape(Rectangle())     // кликается вся строка, не только текст
        }
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
        navigation.open(.memory)
    }
}

#endif
