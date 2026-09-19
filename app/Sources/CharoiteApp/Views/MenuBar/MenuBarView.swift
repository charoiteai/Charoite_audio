import SwiftUI

#if os(macOS)

/// Иконка в системной строке — единственное, что видно ВСЕГДА.
///
/// До №110 критикал записи жил только внутри выпадающего меню: пока человек
/// его не откроет — тишина. С №139 иконка рисует вердикт свёртки здоровья
/// (`HealthRollup` → `HealthPresentation.iconTier`): красный треугольник —
/// данные гибнут при живой записи (отказ диска, мёртвый насос), жёлтый круг —
/// деградация без потери записи (обработка, Ollama, ночь), обычный символ —
/// сигналов нет. Строка меню может отрисовать монохромно, поэтому носитель
/// сигнала — форма символа, цвет — усилитель.
struct MenuBarLabel: View {
    @ObservedObject private var sufler = SuflerService.shared
    @ObservedObject private var processing = MeetingProcessingService.shared
    @ObservedObject private var ollama = OllamaRuntimeService.shared
    @ObservedObject private var nightly = NightlyStatusService.shared

    var body: some View {
        let verdict = MenuBarHealth.verdict(sufler: sufler, processing: processing,
                                            ollama: ollama, nightly: nightly)
        switch HealthPresentation.iconTier(verdict) {
        case .critical:
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.red)
                .accessibilityLabel(HealthPresentation.iconLabel(verdict)
                                    ?? L.t("Критическая ошибка записи", "Critical recording failure", "录音出现严重故障"))
        case .degraded:
            Image(systemName: "exclamationmark.circle")
                .foregroundStyle(Theme.warning)
                .accessibilityLabel(HealthPresentation.iconLabel(verdict)
                                    ?? L.t("Есть что проверить", "Needs attention", "需要注意"))
        case .ok:
            Image(systemName: "brain.head.profile")
        }
    }
}

/// Один вход свёртки для обеих поверхностей меню-бара: иконка и строка
/// читают один вердикт, а не собирают своё из четырёх сервисов. Слова — только
/// владельцев: заголовок ошибки обработки — `errorHeadline`, Ollama —
/// `explanation(for:)`, ночь — `title(for:)`.
enum MenuBarHealth {
    @MainActor
    static func verdict(sufler: SuflerService, processing: MeetingProcessingService,
                        ollama: OllamaRuntimeService, nightly: NightlyStatusService) -> HealthVerdict {
        HealthRollup.rollup(recording: sufler.pipelineHealth.problem,
                            isRecording: sufler.isRunning,
                            processingError: processing.errorHeadline,
                            ollama: ollama.state,
                            nightly: nightly.status.state,
                            nightlyAgentConfigured: nightly.agentConfigured)
    }
}

/// Меню-бар: статус, быстрый вопрос локальной модели, диктовка и заметка.
struct MenuBarView: View {
    @ObservedObject private var sufler = SuflerService.shared
    @ObservedObject private var processing = MeetingProcessingService.shared
    @ObservedObject private var dictation = DictationService.shared
    @ObservedObject private var chat = LocalChatService.shared
    @ObservedObject private var navigation = WorkspaceNavigation.shared
    @ObservedObject private var ollama = OllamaRuntimeService.shared
    @ObservedObject private var nightly = NightlyStatusService.shared
    @State private var quick = ""

    /// Что происходит прямо сейчас — одной строкой и одним цветом.
    ///
    /// Приложение живёт в меню-баре, и окно после встречи обычно закрывают.
    /// Раньше здесь были только «Идёт запись» и «Готов»: всё, что случалось
    /// с встречей после «Стоп» — обработка, готовый результат, ошибка, —
    /// было видно только в окне, то есть чаще всего нигде. С №139 порядок —
    /// у политики представления (`HealthPresentation.menuLine`): идёт работа →
    /// проблема → «Встреча готова» → покой; лежащая Ollama сутки пряталась за
    /// готовой встречей (Important GLM входного круга), а после первой версии
    /// свёртки постоянная жалоба прятала живое «Обрабатываю…» (Critical DS).
    private var state: (text: String, color: Color) {
        let verdict = MenuBarHealth.verdict(sufler: sufler, processing: processing,
                                            ollama: ollama, nightly: nightly)
        let line = HealthPresentation.menuLine(verdict, isRecording: sufler.isRunning,
                                               isProcessing: processing.isProcessing,
                                               processingText: processing.isProcessing ? processing.statusText : nil,
                                               hasReadyMeeting: processing.actionTitle != nil)
        return (line.text, line.tier.map(Self.color(for:)) ?? .accentColor)
    }

    private static func color(for tier: HealthTier) -> Color {
        switch tier {
        case .ok: return Theme.ok
        case .degraded: return Theme.warning
        case .critical: return .red
        }
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
                if sufler.isRunning {
                    // цифры живут в своей вью — секунда не перерисовывает панель
                    RecordingClock(startedAt: sufler.recordingStartedAt)
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        // без label VoiceOver читал цифры дважды: содержимое
                        // Text и его же value (круг-2 DS, M3)
                        .accessibilityLabel(L.t("Идёт запись", "Recording", "录音中"))
                }
            }
            // открытие меню — повод освежить владельцев сразу, не дожидаясь тика
            // планировщика; самих проб во вью нет (Critical GLM входного круга)
            .task { await HealthClock.tick() }

            if let pipelineStatus = sufler.pipelineStatusText {
                Text(pipelineStatus)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(sufler.pipelineStatusIsCritical
                                     ? Color.red : Theme.warning)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }

            // Запись и результат — прямо здесь: за ними не нужно открывать окно.
            HStack(spacing: 10) {
                if sufler.isRunning {
                    Button {
                        SuflerService.shared.stop()
                    } label: {
                        Label(L.t("Остановить", "Stop", "停止"), systemImage: "stop.circle")
                    }
                    .disabled(sufler.isTransitioning)
                } else {
                    Button {
                        navigation.open(.meeting)
                        SuflerService.shared.start()
                    } label: {
                        Label(L.t("Начать запись", "Start recording", "开始录音"),
                              systemImage: "record.circle")
                    }
                    .disabled(processing.isProcessing || sufler.isTransitioning)
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
                .charoite(.link, .s)
                Button(L.t("Сегодня", "Today", "今天")) {
                    navigation.open(.today)
                }
                .charoite(.link, .s)
            }

            HStack(spacing: 8) {
                TextField(L.t("Быстрый вопрос…", "Quick question…", "快速提问…"), text: $quick)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit(sendQuick)
                Button {
                    sendQuick()
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                }
                .charoite(.icon, .s)
                .help(L.t("Отправить вопрос", "Send the question", "发送问题"))
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
                .charoite(.regular, .s)
                Spacer()
                Button {
                    NSApp.terminate(nil)
                } label: {
                    Label(L.t("Выход", "Quit", "退出"), systemImage: "power")
                }
                .charoite(.quiet, .s)
            }
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
