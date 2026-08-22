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
    @ObservedObject private var nightly = NightlyStatusService.shared
    @ObservedObject private var version = VersionStatusService.shared
    @ObservedObject private var updater = UpdateService.shared
    @ObservedObject private var readiness = SetupReadinessService.shared
    // онбординг живёт sheet'ом в суфлёре: прямой старт с «Сегодня» до него
    // обходил бы первый запуск (ревью 15.08) — капсула тогда ведёт в Meeting
    @AppStorage("charoit.firstRunSeen") private var firstRunSeen = false
    // Клон ~/Charoite_audio при вложенном коде больше не берётся сам (см.
    // AppSettings.charoiteRoot). Тем, у кого данные уже там, предлагаем
    // выбрать его явно — один раз, ответ помним; передумать можно в Настройках.
    @AppStorage("charoite.legacyRootPrompted") private var legacyRootPrompted = false
    @State private var showLegacyRootPrompt = false

    var body: some View {
        VStack(spacing: 0) {
            lifecycle
            Divider()
            HSplitView {
                PrepView()
                    .frame(minWidth: 380, maxWidth: .infinity)
                recent
                    // Колонка недавних встреч сжимается первой: подготовка к
                    // встрече важнее списка, а раньше сумма минимумов не
                    // влезала в окно и обе колонки резались краем.
                    .frame(minWidth: 210, idealWidth: 300, maxWidth: 340)
                    .layoutPriority(-1)
            }
        }
        .onAppear {
            if !legacyRootPrompted && AppSettings.legacyCloneAwaitsChoice {
                showLegacyRootPrompt = true
            }
        }
        .alert(L.t("Найдена папка ~/Charoite_audio", "Found the ~/Charoite_audio folder", "发现 ~/Charoite_audio 文件夹"),
               isPresented: $showLegacyRootPrompt) {
            Button(L.t("Использовать её для данных", "Use it for data", "用作数据文件夹")) {
                AppSettings.adoptLegacyCloneAsDataRoot()
                legacyRootPrompted = true
            }
            Button(L.t("Оставить Application Support", "Keep Application Support", "保留 Application Support"),
                   role: .cancel) {
                legacyRootPrompted = true
            }
        } message: {
            Text(L.t("Раньше приложение брало эту папку как папку данных само. Теперь — только по явному выбору: в ней лежит config.yaml с командой после встречи, и подхватывать её молча небезопасно. Код демона в любом случае остаётся из приложения; изменить выбор можно в Настройках.",
                     "The app used to pick this folder as its data folder automatically. Now it takes it only when you choose it explicitly: the folder holds config.yaml with the post-meeting command, and adopting it silently is unsafe. The daemon code stays the bundled one either way; you can change this in Settings.",
                     "此前应用会自动把该文件夹当作数据文件夹。现在只有在您明确选择时才会使用：其中的 config.yaml 含有会后命令，静默采用并不安全。无论如何守护进程代码仍来自应用本身；可随时在设置中更改。"))
        }
    }

    /// Фаза жизненного цикла — чистой функцией ради тестов: сочетание
    /// «готовый результат + можно начать следующую» без теста уже терялось
    /// (ready-снимок висит сутки и прятал бы запись с «Сегодня» — ревью 15.08).
    enum LifecyclePhase: Equatable {
        case record            // капсула-старт + строка готовности
        /// Направление перехода — из машины состояний, а не из isRunning:
        /// при остановке isRunning уже false, и капсула показывала бы
        /// «Запускаю…» на живой остановке (ревью 15.08 ×2).
        case transitioning(stopping: Bool)
        case recording         // капсула «Стоп» + ссылка «Открыть встречу»
        case processing        // статус + «Показать статус», капсулы нет:
                               // запись и конвейер конкурируют за модели
        case readyPlusRecord   // «Открыть результат» И капсула следующей записи
    }

    static func lifecyclePhase(recording: RecordingLifecycle,
                               isProcessing: Bool, hasReadyResult: Bool) -> LifecyclePhase {
        switch recording {
        case .starting: return .transitioning(stopping: false)
        case .stopping: return .transitioning(stopping: true)
        case .recording: return .recording
        case .idle:
            if isProcessing { return .processing }
            if hasReadyResult { return .readyPlusRecord }
            return .record
        }
    }

    private var phase: LifecyclePhase {
        Self.lifecyclePhase(recording: sufler.lifecycle,
                            isProcessing: processing.isProcessing,
                            hasReadyResult: processing.snapshot?.state == .ready)
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
            switch phase {
            case .record:
                capsuleWithReadiness
            case .transitioning(let stopping):
                RecordCapsule(isRecording: stopping,
                              isTransitioning: true,
                              clock: "", action: {})
            case .recording:
                Button(L.t("Открыть встречу", "Open meeting", "打开会议")) {
                    navigation.open(.meeting)
                }
                .charoite(.link)
                VStack(alignment: .trailing, spacing: 6) {
                    RecordCapsule(isRecording: true,
                                  clock: SuflerService.clockText(sufler.recordingElapsed),
                                  action: { sufler.toggle() })
                    if sufler.stopConfirmPending {
                        // двухшаговый стоп короткой записи: подтверждение
                        // видно там, где нажали, а не в статусе суфлёра
                        Text(L.t("«Стоп» ещё раз, чтобы точно остановить",
                                 "Press Stop again to confirm",
                                 "再按一次停止以确认"))
                            .font(.caption.weight(.medium))
                            .foregroundStyle(Theme.warning)
                    }
                }
            case .processing:
                ProgressView().controlSize(.small)
                Button(L.t("Показать статус", "Show status", "查看状态")) {
                    navigation.open(.meeting)
                }
                .charoite(.prominent)
            case .readyPlusRecord:
                Button(L.t("Открыть результат", "Open result", "打开结果")) {
                    if let ready = processing.snapshot {
                        navigation.open(.meetings, meetingID: ready.meetingID)
                    }
                }
                .charoite(.regular)
                capsuleWithReadiness
            }
        }
        .padding(.horizontal, 18).padding(.vertical, 14)
        .background(Theme.accent.opacity(0.055))
        .onAppear { refreshReadinessIfIdle() }
        // onAppear не срабатывает при смене фазы внутри открытого «Сегодня»:
        // запись остановилась — статус готовности не должен остаться
        // вчерашним (ревью 15.08 ×3); от лишних probe защищает TTL сервиса
        .onChange(of: phase) { _, _ in refreshReadinessIfIdle() }
    }

    /// Готовность нужна только там, где есть капсула старта; во время
    /// записи/обработки лишние probe-проверки (Python, Ollama) не нужны.
    private func refreshReadinessIfIdle() {
        switch phase {
        case .record, .readyPlusRecord: readiness.refresh()
        default: break
        }
    }

    /// Капсула старта со строкой готовности под ней — от реальных проверок
    /// SetupReadinessService, а не от самочувствия интерфейса.
    private var capsuleWithReadiness: some View {
        VStack(alignment: .trailing, spacing: 6) {
            RecordCapsule(isRecording: false, clock: "") {
                navigation.open(.meeting)
                // первый запуск: онбординг-sheet суфлёра сам предложит старт
                if firstRunSeen { sufler.toggle() }
            }
            ReadinessLine(snapshot: readiness.snapshot,
                          isChecking: readiness.isChecking)
        }
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
            Divider()
            nightlyRow
            if version.needsAttention { versionRow }
        }
        .padding(14)
        .onAppear {
            nightly.refresh()
            version.refresh()
        }
    }

    /// Прошла ли ночная обработка графа.
    ///
    /// Ночью Чароит правит ядра, собирает досье и пишет утренний бриф —
    /// работа по определению невидимая. Раньше о том, что она перестала
    /// выполняться, можно было узнать только через месяц по несвежему
    /// графу или заглянув в лог в /tmp. Успешный прогон — одна спокойная
    /// строка, всё остальное подсвечено.
    private var nightlyRow: some View {
        HStack(spacing: 8) {
            Image(systemName: nightly.icon)
                .font(.caption)
                .foregroundStyle(nightly.needsAttention ? Theme.warning : Color.secondary)
            VStack(alignment: .leading, spacing: 1) {
                Text(nightly.title)
                    .font(.caption.weight(nightly.needsAttention ? .medium : .regular))
                Text(nightly.detail)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .help(nightly.detail)
    }

    /// Что происходит с обновлением прямо сейчас — вместо описания версии.
    /// Отказ и ошибку показываем на месте: диалог поверх экрана ради строки
    /// «идёт запись» человек закрывает не читая.
    private var updateNote: String? {
        switch updater.stage {
        case .idle:
            // Сборка без Developer ID: обновление сбросит доступы к микрофону
            // и экрану — сказать до кнопки, а не после перезапуска.
            if case .updateAvailable = version.status.state { return UpdateService.adHocSignatureNote }
            return nil
        case .downloading(let percent):
            return L.t("Скачиваю… \(percent)%", "Downloading… \(percent)%", "下载中… \(percent)%")
        case .verifying:
            return L.t("Проверяю контрольную сумму", "Verifying checksum", "正在校验")
        case .installing:
            return L.t("Ставлю и перезапускаюсь", "Installing and restarting", "正在安装并重启")
        case .refused(let reason), .failed(let reason):
            return reason
        }
    }

    /// Та ли версия работает.
    ///
    /// Показывается, только когда есть расхождение: приложение отстало от
    /// выпуска или — что хуже — код в рабочей папке не той версии, что
    /// приложение. Совпадение версий это норма, и напоминать о норме
    /// каждый день значит приучить не читать строку вовсе.
    private var versionRow: some View {
        HStack(spacing: 8) {
            Image(systemName: version.icon)
                .font(.caption)
                .foregroundStyle(Theme.warning)
            VStack(alignment: .leading, spacing: 1) {
                Text(version.title).font(.caption.weight(.medium))
                Text(updateNote ?? version.detail)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            if case .updateAvailable(_, let latest) = version.status.state, !updater.isBusy {
                Button(L.t("Обновить", "Update", "更新")) {
                    Task { await updater.install(tag: "v\(latest)") }
                }
                .charoite(.link, .s)
            }
        }
        .help(version.detail)
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
