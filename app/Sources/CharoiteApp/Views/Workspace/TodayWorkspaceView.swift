import SwiftUI

#if os(macOS)

/// Домашний экран меняется вместе с жизненным циклом встречи.
struct TodayContentLayout: Equatable {
    let isWide: Bool
    let outerScrollAxes: Axis.Set
    let columnScrollAxes: Axis.Set
    let columnHeight: CGFloat?
    let dividerWidth: CGFloat?
    let dividerHeight: CGFloat
    let recentWidth: CGFloat?

    static func resolve(for size: CGSize) -> TodayContentLayout {
        let wide = size.width >= 780
        let columnHeight = max(380, size.height)
        return TodayContentLayout(
            isWide: wide,
            outerScrollAxes: wide ? [] : .vertical,
            columnScrollAxes: wide ? .vertical : [],
            columnHeight: wide ? columnHeight : nil,
            dividerWidth: wide ? 1 : nil,
            dividerHeight: wide ? columnHeight : 1,
            recentWidth: wide ? 300 : nil
        )
    }
}

extension TodayWorkspaceView {
    static func contentLayout(for size: CGSize) -> TodayContentLayout {
        TodayContentLayout.resolve(for: size)
    }
}

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
            GeometryReader { geometry in
                let spec = Self.contentLayout(for: geometry.size)
                let layout = spec.isWide
                    ? AnyLayout(HStackLayout(alignment: .top, spacing: 0))
                    : AnyLayout(VStackLayout(alignment: .leading, spacing: 0))
                // Preserve child identity across resizing: Prep owns a task
                // and transient search state, so an if/else must not recreate it.
                // Wide mode disables the outer scroll and scrolls each column
                // independently; narrow mode disables inner column scroll so one
                // outer ScrollView scrolls the stacked page without a scroll trap.
                ScrollView(spec.outerScrollAxes) {
                    layout {
                        PrepView(scrollable: spec.isWide)
                            .frame(maxWidth: .infinity)
                            .frame(height: spec.columnHeight)
                        Rectangle()
                            .fill(Color.primary.opacity(0.08))
                            .frame(width: spec.dividerWidth,
                                   height: spec.dividerHeight)
                        ScrollView(spec.columnScrollAxes) {
                            recent
                        }
                        .frame(width: spec.recentWidth,
                               height: spec.columnHeight)
                    }
                    .frame(minHeight: geometry.size.height, alignment: .topLeading)
                }
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
        VStack(alignment: .leading, spacing: 16) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .center, spacing: 24) {
                    lifecycleSummary.frame(minWidth: 240)
                    Spacer(minLength: 0)
                    lifecycleActions.fixedSize(horizontal: true, vertical: false)
                }
                VStack(alignment: .leading, spacing: 16) {
                    lifecycleSummary
                    lifecycleActions
                }
            }
            // Confirmation must not change the action row's intrinsic width:
            // the second Stop press should land on the same button.
            if phase == .recording && sufler.stopConfirmPending {
                Text(L.t("«Стоп» ещё раз, чтобы точно остановить",
                         "Press Stop again to confirm",
                         "再按一次停止以确认"))
                    .font(.caption.weight(.medium))
                    .foregroundStyle(Theme.warning)
                    .multilineTextAlignment(.trailing)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .trailing)
            }
            if phase == .record || phase == .readyPlusRecord {
                Divider()
                recordingReadiness
            }
        }
        .padding(20)
        .background(Theme.accent.opacity(0.045))
        .onAppear { refreshReadinessIfIdle() }
        .onChange(of: phase) { _, _ in refreshReadinessIfIdle() }
    }

    private var lifecycleSummary: some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: lifecycleIcon)
                .font(.title2)
                .foregroundStyle(Theme.accent)
                .frame(width: 44, height: 44)
                .background(Theme.accent.opacity(0.10),
                            in: RoundedRectangle(cornerRadius: Theme.radiusCard))
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 5) {
                Text(lifecycleTitle).font(.title2.weight(.semibold))
                Text(lifecycleDetail)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var lifecycleActions: some View {
        HStack(spacing: 12) {
            switch phase {
            case .record:
                recordButton
            case .transitioning(let stopping):
                RecordCapsule(isRecording: stopping,
                              isTransitioning: true,
                              clockFrom: nil, action: {})
            case .recording:
                Button(L.t("Открыть встречу", "Open meeting", "打开会议")) {
                    navigation.open(.meeting)
                }
                .charoite(.link)
                RecordCapsule(isRecording: true,
                              clockFrom: sufler.recordingStartedAt,
                              action: { sufler.toggle() })
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
                recordButton
            }
        }
    }

    /// Готовность нужна только там, где есть капсула старта; во время
    /// записи/обработки лишние probe-проверки (Python, Ollama) не нужны.
    private func refreshReadinessIfIdle() {
        switch phase {
        case .record, .readyPlusRecord: readiness.refresh()
        default: break
        }
    }

    private var recordButton: some View {
        RecordCapsule(isRecording: false, clockFrom: nil) {
            navigation.open(.meeting)
            // The Meeting sheet owns first-run setup before recording.
            if firstRunSeen { sufler.toggle() }
        }
    }

    private var recordingReadiness: some View {
        VStack(alignment: .leading, spacing: 10) {
            ReadinessLine(snapshot: readiness.snapshot,
                          isChecking: readiness.isChecking)
            // Keep installation failures and recovery actions visible, but
            // separate these utilities from the primary recording action.
            HStack(alignment: .top, spacing: 12) {
                if !ModelPullService.diarizationInstalled {
                    DiarizationInstallButton(alignment: .leading)
                }
                NemotronTodayButton()
                Spacer(minLength: 0)
            }
        }
    }

    private var recent: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text(L.t("Недавние встречи", "Recent meetings", "近期会议"))
                    .font(.headline)
                Spacer()
                Button(L.t("Все", "All", "全部")) { navigation.open(.meetings) }
                    .charoite(.link, .s)
            }
            if repository.records.isEmpty {
                EmptyState(L.t("Результатов пока нет", "No results yet", "还没有结果"),
                           text: L.t("Три последние встречи появятся здесь, когда закончится обработка первой записи.",
                                     "Your three latest meetings appear here once the first recording is processed.",
                                     "第一条录音处理完成后，最近三场会议会显示在这里。"),
                           inset: false)
                Spacer()
            } else {
                ForEach(repository.records.prefix(3)) { record in
                    Button {
                        navigation.open(.meetings, meetingID: record.id)
                    } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(record.title).font(.callout.weight(.medium)).lineLimit(2)
                            recentNote(record)
                            Text(record.card.gist ?? compactState(record.snapshot))
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
                        .padding(14)
                        .background(.background,
                                    in: RoundedRectangle(cornerRadius: Theme.radiusCard))
                        .overlay {
                            RoundedRectangle(cornerRadius: Theme.radiusCard)
                                .strokeBorder(Color.primary.opacity(0.08), lineWidth: 1)
                        }
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
        .padding(18)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Color.primary.opacity(0.025))
        .onAppear {
            // свежесть ночи — у планировщика владельцев, не у вью (второй путь
            // мимо коалесценции — Important DS круга 3 по №139)
            HealthClock.requestTick()
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
            // строка показывается всегда (успешная ночь — одна спокойная строка);
            // цвет и вес — по той же политике, что у иконки меню-бара: `.never` без
            // агента — не настроено, не тревога (Important DS круга 2 по №139)
            let problem = NightlyStatus.isProblem(nightly.status.state, agentConfigured: nightly.agentConfigured)
            Image(systemName: nightly.icon)
                .font(.caption)
                .foregroundStyle(problem ? Theme.warning : Color.secondary)
            VStack(alignment: .leading, spacing: 1) {
                Text(nightly.title)
                    .font(.caption.weight(problem ? .medium : .regular))
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
        if processing.snapshot?.state == .ready {
            return readyNote == nil ? "checkmark.circle.fill" : "exclamationmark.circle.fill"
        }
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
        if let status = processing.activityText { return status }   // слот работы — только работа (Minor GLM круга 3)
        if let ready = processing.snapshot, ready.state == .ready { return readyNote ?? ready.title }
        if let event = calendar.today.first {
            return L.t("Ближайшая встреча · \(Self.time(event.start))",
                       "Next meeting · \(Self.time(event.start))",
                       "下一场会议 · \(Self.time(event.start))")
        }
        switch calendar.accessGranted {
        case .none:
            return L.t("Календарь не подключён — подготовка к встречам выключена.",
                       "Calendar is not connected — meeting prep is off.",
                       "日历未连接——会前准备已关闭。")
        case .some(false):
            return L.t("Доступа к календарю нет — подготовка к встречам выключена.",
                       "No calendar access — meeting prep is off.",
                       "没有日历访问权限——会前准备已关闭。")
        case .some(true):
            return L.t("Новых встреч в календаре сегодня нет.",
                       "No more calendar meetings today.",
                       "今天日历中没有更多会议。")
        }
    }

    /// Пометка последней встречи (№500): «имена не определены», «пересборка не
    /// завершена…» — значок и подпись карточки дня говорят о ней.
    private var readyNote: String? {
        processing.snapshot.flatMap(MeetingProcessingPolicy.readyNote(for:))
    }

    /// Пометка недавней встречи — над сутью, суть остаётся. Сути нет — пометку
    /// несёт `compactState`, второй строкой её не повторяем.
    @ViewBuilder
    private func recentNote(_ record: MeetingRecord) -> some View {
        if record.card.gist != nil, record.state == .ready,
           let note = MeetingProcessingPolicy.readyNote(for: record.snapshot) {
            Text(note).font(.caption).foregroundStyle(Theme.warning).lineLimit(2)
        }
    }

    private func compactState(_ snapshot: MeetingProcessingSnapshot) -> String {
        switch MeetingProcessingPolicy.resolvedState(snapshot) {
        case .ready: return MeetingProcessingPolicy.readyText(for: snapshot)
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
