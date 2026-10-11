import Combine
import Foundation

#if os(macOS)

/// Процесс кнопки Nemotron — `LongProcess` в бою, подделка в тестах.
protocol NemotronProcess: AnyObject, Sendable {
    func run() async -> LongProcessOutcome
    func cancel(grace: TimeInterval)
}

extension LongProcess: NemotronProcess {}

/// Единый приёмник действий кнопки Nemotron (№622): установка, проба
/// `--check` перед включением, выключение и план `--plan`.
///
/// Две независимые переменные: `action` (что делает кнопка) и `plan` (что
/// известно о движке). Занятость, «Отмена» и выход из приложения читают только
/// `action`; план `action` не меняет никогда — иначе перепроверка посреди
/// установки прятала «Отмену», и Cmd-Q оставлял pip сиротой (сверка in2 №5).
@MainActor
final class NemotronEngineService: ObservableObject {
    static let shared = NemotronEngineService()

    /// Что делает кнопка. План (`plan`) эту переменную не меняет никогда.
    enum Action: Equatable {
        case idle
        case running(NemotronActionKind, lastLine: String)
        case cancelling(afterCommit: Bool)
        case outcome(ActionOutcome)

        /// Идёт процесс: «Отмена», занятость и выход из приложения читают это.
        var isActive: Bool {
            switch self {
            case .running, .cancelling: return true
            case .idle, .outcome: return false
            }
        }
    }

    /// Итог действия поверх кнопок нового плана.
    struct ActionOutcome: Equatable {
        var text: String
        var success: Bool
        var offerEnable = false
        var offerReinstall = false
    }

    typealias ProcessFactory = @MainActor (_ arguments: [String], _ root: URL,
                                           _ onLine: (@MainActor (String) -> Void)?,
                                           _ onCleanupAfterCommit: (@MainActor () -> Void)?) -> NemotronProcess

    /// Срок между SIGTERM и SIGKILL у «Отмены» (развилка 2).
    static let cancelGrace: TimeInterval = 30
    /// Тот же срок при выходе из приложения.
    static let terminationGrace: TimeInterval = 10
    /// Потолок ожидания EOF у боевого помощника.
    static let eofCeiling: TimeInterval = 2

    @Published var action: Action = .idle
    @Published private(set) var plan: NemotronPlanState = .none

    var isActive: Bool { action.isActive }

    /// Выход из приложения уже решён — новые действия не начинаются: их
    /// никто не отменил бы (сверка in2 №16). Ставит `AppDelegate`.
    var terminationInProgress = false

    private let makeProcess: ProcessFactory
    private let currentRoot: @MainActor () -> URL
    private let isRecording: @MainActor () -> Bool
    private let voiceSetInstalling: @MainActor () -> Bool
    private let refreshReadiness: @MainActor () -> Void

    /// Процесс действия жив: «Отмена» горит только при нём. После выхода
    /// процесса решение ещё ждёт `--plan`, но отменять уже нечего (сверка out1 №8).
    @Published private(set) var processRunning = false

    private var current: NemotronProcess? {
        didSet { processRunning = current != nil }
    }
    private var subscriptions: Set<AnyCancellable> = []

    // single-flight плана
    private var planInFlight = false
    private var planAgain = false
    private var planDeferred = false
    private var plansStarted = 0
    private var planWaiters: [(minRun: Int, resume: (NemotronPlanState) -> Void)] = []
    private var activeEndWaiters: [() -> Void] = []
    private var lastRecording = false
    private var lastVoiceSetInstalling = false

    /// Сколько процессов `--plan` запущено (для тестов single-flight).
    var planRuns: Int { plansStarted }

    init(makeProcess: @escaping ProcessFactory = NemotronEngineService.liveProcess,
         currentRoot: @escaping @MainActor () -> URL = { AppSettings.charoiteRoot },
         isRecording: @escaping @MainActor () -> Bool = { SuflerService.shared.hasActiveLifecycle },
         voiceSetInstalling: @escaping @MainActor () -> Bool = {
             ModelPullService.shared.progress[ModelPullService.diarizationKey] != nil
         },
         refreshReadiness: @escaping @MainActor () -> Void = { SetupReadinessService.shared.refresh(force: true) },
         lifecycleChanges: AnyPublisher<Void, Never>? = nil,
         voiceSetChanges: AnyPublisher<Void, Never>? = nil) {
        self.makeProcess = makeProcess
        self.currentRoot = currentRoot
        self.isRecording = isRecording
        self.voiceSetInstalling = voiceSetInstalling
        self.refreshReadiness = refreshReadiness
        // @Published шлёт значение до записи: пересчёт — следующим ходом цикла,
        // когда `hasActiveLifecycle` уже видит новое состояние.
        let lifecycle = lifecycleChanges
            ?? SuflerService.shared.$lifecycle.map { _ in () }.eraseToAnyPublisher()
        lifecycle
            .sink { [weak self] _ in Task { @MainActor in self?.recordingMaybeEnded() } }
            .store(in: &subscriptions)
        let voiceSet = voiceSetChanges
            ?? ModelPullService.shared.$progress.map { _ in () }.eraseToAnyPublisher()
        voiceSet
            .sink { [weak self] _ in Task { @MainActor in self?.voiceSetMaybeFinished() } }
            .store(in: &subscriptions)
    }

    nonisolated static let liveProcess: ProcessFactory = { arguments, root, onLine, onCleanup in
        LongProcess(arguments: [AppSettings.scriptPath("scripts/install_engine.py", root: root)] + arguments,
                    root: root, eofCeiling: eofCeiling, onLine: onLine, onCleanupAfterCommit: onCleanup)
    }

    // MARK: план

    /// Внешний запрос плана (появление вида, «Повторить»). Во время действия
    /// не запускает ничего: план придёт после исхода сам, а 190 МБ весов рядом
    /// с pip не читаются.
    func requestPlan() {
        guard !action.isActive else { return }
        if isRecording() {
            planDeferred = true
            return
        }
        planDeferred = false
        if planInFlight { planAgain = true } else { startPlan() }
    }

    /// Появление вида: план нужен, если его нет или прошлый не получен.
    func requestPlanIfNeeded() {
        switch plan {
        case .none, .failed: requestPlan()
        case .planning, .ready: break
        }
    }

    /// Внутренний шаг службы: план, запущенный не раньше этого запроса.
    /// План, стартовавший до конца установки, для решения о ключе устарел.
    func freshPlan() async -> NemotronPlanState {
        let minRun = plansStarted + 1
        return await withCheckedContinuation { cont in
            planWaiters.append((minRun, { cont.resume(returning: $0) }))
            if planInFlight { planAgain = true } else { startPlan() }
        }
    }

    private func startPlan() {
        planInFlight = true
        plansStarted += 1
        let run = plansStarted
        plan = .planning(previous: plan.effective)
        let process = makeProcess(["nemotron", "--plan"], currentRoot(), nil, nil)
        Task { @MainActor [weak self] in
            let outcome = await Self.runDetached(process)
            self?.planFinished(run, NemotronPlan.parse(outcome))
        }
    }

    private func planFinished(_ run: Int, _ state: NemotronPlanState) {
        plan = state
        planInFlight = false
        let ready = planWaiters.filter { $0.minRun <= run }
        planWaiters.removeAll { $0.minRun <= run }
        for w in ready { w.resume(state) }
        if planAgain || !planWaiters.isEmpty {
            planAgain = false
            startPlan()
        }
    }

    private func recordingMaybeEnded() {
        let now = isRecording()
        defer { lastRecording = now }
        guard lastRecording, !now, planDeferred else { return }
        requestPlan()
    }

    private func voiceSetMaybeFinished() {
        let now = voiceSetInstalling()
        defer { lastVoiceSetInstalling = now }
        // Набор голосов поставлен — план другой (`voice_set` в размерах).
        if lastVoiceSetInstalling && !now { requestPlan() }
    }

    nonisolated private static func runDetached(_ p: NemotronProcess) async -> LongProcessOutcome {
        await Task.detached(priority: .utility) { await p.run() }.value
    }

    // MARK: действия

    /// «Поставить и включить» (и «Переставить»).
    func startInstall() { start(.install) }

    /// «Включить»: проба `--check`, потом условная запись ключа.
    func startEnable() { start(.check) }

    /// Почему действие сейчас не начнётся; `nil` — начнётся.
    func startRefusal() -> String? {
        if action.isActive || voiceSetInstalling() { return EngineInstallActivity.busyText }
        if isRecording() { return NemotronVisibleActions.recordingText }
        if terminationInProgress { return L.t("приложение закрывается", "the app is quitting", "应用正在退出") }
        // Без config.yaml ключ после установки некуда записать (сверка out1 №4):
        // файл создаёт мастер кнопкой «Сохранить».
        if !FileManager.default.fileExists(atPath: currentRoot().appendingPathComponent("config/config.yaml").path) {
            return L.t("сначала сохраните настройки — config.yaml ещё нет",
                       "save the settings first — config.yaml does not exist yet",
                       "请先保存设置——config.yaml 尚不存在")
        }
        return nil
    }

    private func start(_ kind: NemotronActionKind) {
        guard startRefusal() == nil else { return }
        // Корень берётся один раз: помощник, чтение ключа и запись — в нём (сверка №8).
        let root = currentRoot()
        let r0 = AppSettings.configValue("diarize_backend", root: root)
        let arguments = kind == .install ? ["nemotron"] : ["nemotron", "--check"]
        let process = makeProcess(arguments, root, { [weak self] line in
            guard let self, case .running(let k, _) = self.action else { return }
            self.action = .running(k, lastLine: line)
        }, { [weak self] in
            guard let self, case .cancelling = self.action else { return }
            self.action = .cancelling(afterCommit: true)
        })
        current = process
        action = .running(kind, lastLine: "")
        Task { @MainActor [weak self] in
            let outcome = await Self.runDetached(process)
            await self?.finish(kind, outcome: outcome, root: root, r0: r0)
        }
    }

    private func finish(_ kind: NemotronActionKind, outcome: LongProcessOutcome, root: URL, r0: String?) async {
        current = nil
        let r1 = AppSettings.configValue("diarize_backend", root: root)
        // Корень сверяется на каждом решении, а не до `await freshPlan()`: папку
        // данных могут сменить, пока идёт план, и CAS ушёл бы в старый корень
        // (сверка out1 C3). Между последним решением и CAS `await` нет.
        func decide(_ allowed: Bool, _ reason: String, _ cas: AppSettings.ConfigCAS?) -> NemotronDecision {
            NemotronDecision.decide(outcome: outcome, kind: kind, rootChanged: currentRoot() != root, r0: r0, r1: r1,
                                    enableAllowed: allowed, enableReason: reason, cas: cas)
        }
        var decision = decide(true, "", nil)
        var planDone = false
        if decision == .attemptWrite, isRecording() || terminationInProgress {
            // Во время записи `--plan` не идёт (сверка out1 C2); при выходе из
            // приложения его никто не дождётся (№8). Ключ не пишем — «Включить»
            // после встречи; план для показа — по концу записи (`requestPlan`
            // в хвосте его откладывает).
            decision = .stop(isRecording()
                ? L.t("поставлено; включу после встречи — нажмите «Включить»",
                      "installed; enable after the meeting — press “Enable”", "已安装；会议结束后请点击“启用”")
                : L.t("приложение закрывается — не включено", "the app is quitting — not enabled", "应用正在退出——未启用"),
                offerEnable: true, offerReinstall: false)
        } else if decision == .attemptWrite {
            let state = await freshPlan()
            planDone = true
            if case .ready(let p) = state {
                decision = decide(p.offer.enable.allowed, p.offer.enable.reason ?? "", nil)
                if decision == .attemptWrite {
                    let cas = AppSettings.setConfigValueIfUnchanged("diarize_backend", expected: r1,
                                                                     value: "nemotron", root: root)
                    decision = decide(true, "", cas)
                    if case .written = decision { planDone = false }
                }
            } else if case .failed(let text) = state {
                decision = .stop(text, offerEnable: true, offerReinstall: false)
            }
        }
        setOutcome(Self.outcome(of: decision))
        refreshReadiness()
        // Через `requestPlan`: во время записи он откладывает план до её конца.
        if !planDone && !terminationInProgress { requestPlan() }
    }

    static func outcome(of decision: NemotronDecision) -> ActionOutcome {
        switch decision {
        case .written(let text):
            return ActionOutcome(text: text, success: true)
        case .stop(let text, let enable, let reinstall):
            return ActionOutcome(text: text, success: false, offerEnable: enable, offerReinstall: reinstall)
        case .attemptWrite:
            return ActionOutcome(text: "", success: false)
        }
    }

    private func setOutcome(_ o: ActionOutcome) {
        action = .outcome(o)
        let waiters = activeEndWaiters
        activeEndWaiters.removeAll()
        for w in waiters { w() }
    }

    /// «Выключить»: условная запись `sherpa` по сырому значению, без пробы.
    func disable() {
        guard !action.isActive, !terminationInProgress,
              case .ready(let p) = plan, p.offer.disable.allowed else { return }
        let root = currentRoot()
        let r0 = AppSettings.configValue("diarize_backend", root: root)
        let cas = AppSettings.setConfigValueIfUnchanged("diarize_backend", expected: r0, value: "sherpa", root: root)
        switch cas {
        case .written:
            setOutcome(ActionOutcome(text: L.t("Nemotron выключен — голоса размечает sherpa",
                                               "Nemotron is disabled — sherpa labels voices",
                                               "Nemotron 已停用——由 sherpa 标注说话人"), success: true))
        case .changed:
            setOutcome(ActionOutcome(text: L.t("конфиг изменили — не выключено, проверьте ещё раз",
                                               "the config changed — not disabled, check again",
                                               "配置已被修改——未停用，请再检查"), success: false))
        case .failed(let reason):
            setOutcome(ActionOutcome(text: L.t("не удалось записать config.yaml: ", "could not write config.yaml: ",
                                               "无法写入 config.yaml：") + reason, success: false))
        }
        refreshReadiness()
        requestPlan()
    }

    /// Вид кнопки ушёл с экрана: исход снят, «Сегодня» снова считает по плану
    /// (сверка out1 C1). Исход с «Включить» или «Переставить» остаётся до
    /// следующего действия — иначе кнопка после неудачной пробы пропала бы.
    func dismissOutcome() {
        if case .outcome(let o) = action, o.success || !(o.offerEnable || o.offerReinstall) {
            action = .idle
        }
    }

    /// «Отмена»: SIGTERM группе, SIGKILL через `cancelGrace`.
    func cancel() {
        guard case .running = action, let current else { return }
        action = .cancelling(afterCommit: false)
        current.cancel(grace: Self.cancelGrace)
    }

    /// Выход из приложения: отмена со сроком 10 с; `done` — когда действие
    /// кончилось (сразу, если его нет).
    func cancelForTermination(done: @escaping () -> Void) {
        guard action.isActive else { return done() }
        activeEndWaiters.append(done)
        if case .running = action { action = .cancelling(afterCommit: false) }
        current?.cancel(grace: Self.terminationGrace)
    }
}

#endif
