import AVFoundation
import Foundation

#if os(macOS)
import AppKit

/// Мост к локальному суфлёру (папка установки Charoite_audio): запускает
/// python-демон, читает NDJSON-события из stdout, шлёт команды в stdin.
/// Всё локально: аудио → STT → Ollama, ничего не покидает машину.
@MainActor
final class SuflerService: ObservableObject {
    // один сервис на приложение: чат смотрит isRunning, чтобы не вставать
    // в очередь Ollama за встречными генерациями большой модели
    static let shared = SuflerService()
    struct TranscriptLine: Identifiable, Equatable {
        let id = UUID()
        let ts: String
        var speaker: String = ""
        var text: String
    }

    @Published private(set) var isRunning = false
    @Published private(set) var lifecycle: RecordingLifecycle = .idle

    var isTransitioning: Bool { lifecycle.isTransitioning }
    var hasActiveLifecycle: Bool {
        RecordingLifecyclePolicy.isActive(lifecycle, daemonAlive: process?.isRunning == true)
    }

    /// Когда началась текущая запись. Не nil ровно тогда, когда идёт встреча.
    ///
    /// «Идёт запись» без времени — обещание без доказательства: человек,
    /// который вернулся к ноутбуку через час, не может отличить работающую
    /// запись от зависшего индикатора. Часы отсчитывают от старта демона,
    /// а не от первой реплики: тишина в начале встречи тоже записана.
    @Published private(set) var recordingStartedAt: Date?

    @Published var status = L.t("Готов к запуску", "Ready", "就绪")
    /// Текущий статус — про сбой, а не про обычный ход дела.
    ///
    /// Раньше это определялось поиском подстрок в самом статусе («прервалась»,
    /// «Не удалось»), а статусы локализованы: у англо- и китаеязычного
    /// пользователя сообщение об оборванной записи выводилось мелким серым
    /// текстом в одну строку и обрезалось. Признак должен приходить из
    /// модели, а не угадываться по переводу.
    @Published var statusIsError = false

    /// Ошибка от самого демона (`status` c `error: true`), не Swift-`fail()`:
    /// только ей позволено заменять критикал на экране (круг-2 DS, I1).
    @Published private(set) var statusErrorFromDaemon = false

    /// Структурное здоровье живого конвейера. Обычные `status`-события его
    /// не сбрасывают: сообщение про обновлённые минутки не имеет права скрыть
    /// отказ записи на диск или продолжающееся отставание STT.
    @Published private(set) var pipelineHealth = PipelineHealthMonitor()

    /// Ставит статус и помечает его как сообщение об отказе.
    func fail(_ text: String) {
        status = text
        statusIsError = true
        statusErrorFromDaemon = false
    }
    @Published var lines: [TranscriptLine] = []
    @Published var hint = ""

    /// Нить встречи: то, что читают, пока разговор идёт.
    ///
    /// Приходит целиком при каждом изменении — демон дописывает её у себя и
    /// присылает готовый вид. Это не лента: старое не уезжает вверх, а стоит
    /// на месте, и глазу не приходится каждый раз искать, что изменилось.
    @Published var thread = ""
    @Published var isHinting = false
    /// Идёт явный поиск прошлого контекста по кнопке ⏮.
    /// Отдельно от isHinting: подсказка и архив могут выполняться параллельно,
    /// но два архивных разбора одной темы одновременно не нужны.
    @Published var cloud = ""          // ответ Claude (Sonnet) — третья панель
    @Published var isClouding = false

    // Живые тумблеры контуров. Выбор запоминается и становится дефолтом
    // следующих встреч (UserDefaults) — «один раз отказался, и так дальше».
    @Published var hintsOn = UserDefaults.standard.object(forKey: "sufler.hints") as? Bool ?? true {
        didSet { applyToggle("hints", hintsOn) }
    }
    // Облако — единственный тумблер, который отправляет стенограмму с машины,
    // поэтому его дефолт «выключено», а не «включено», как у локальных
    // контуров: PRIVACY.md обещает opt-in, и первое включение делает человек.
    // Уже сделанный выбор из UserDefaults уважается как есть.
    @Published var cloudOn = UserDefaults.standard.object(forKey: "sufler.cloud") as? Bool ?? false {
        didSet { applyToggle("cloud", cloudOn) }
    }

    private func applyToggle(_ key: String, _ on: Bool) {
        UserDefaults.standard.set(on, forKey: "sufler.\(key)")
        send("set \(key) \(on ? "on" : "off")")
    }

    /// Часы записи. Секундного таймера здесь больше нет: цифры рисует
    /// `RecordingClock` (TimelineView) от даты старта, поэтому каждую
    /// секунду перерисовываются только они. Секундный @Published
    /// перерисовывал целиком TodayWorkspaceView с десятью ObservedObject
    /// всю встречу — главное слагаемое 37% CPU за 4,5 дня аптайма (№50).
    /// Длительность считается от даты старта, а не накоплением тиков,
    /// поэтому уснувший ноутбук её не «съедает».
    private func startClock() {
        recordingStartedAt = Date()
    }

    func stopClock() {
        recordingStartedAt = nil
    }

    /// Формат живёт в DesignKit рядом с RecordingClock (круг-1 DS, M3:
    /// дизайн-слой не должен тянуть сервис записи ради формата строки);
    /// делегат оставлен, чтобы не трогать существующие вызовы и тесты.
    nonisolated static func clockText(_ seconds: TimeInterval) -> String {
        RecordingClock.text(seconds)
    }

    /// Слишком короткая запись — скорее всего промах по кнопке.
    nonisolated static let tooShortToStop: TimeInterval = 20

    var process: Process?
    private var stdinPipe: Pipe?
    private var stdoutHandle: FileHandle?  // для снятия readabilityHandler при смерти демона
    private var errHandle: FileHandle?     // daemon.err.log — закрывать, иначе fd-утечка на рестартах
    /// Системный звук без BlackHole. Живёт ровно столько же, сколько демон:
    /// поднимается перед стартом, гасится в stop() и при смерти демона —
    /// иначе устройство останется висеть в системе.
    var systemAudioTap: Any?
    /// Захват ScreenCaptureKit — основной путь к системному звуку с 07.08.
    /// Живёт столько же, сколько демон: поднимается перед стартом, гаснет
    /// в stop() и при смерти демона.
    var systemAudioCapture: Any?
    private var stdoutBuffer = Data()
    private var _hintBuf = ""            // буфер троттла подсказки (см. consume)
    // Панель различает содержимое подсказки: авто-контент (бриф, автоподсказки
    // раз в 75с) нить вправе вытеснить, а ответ на РУЧНОЙ вопрос/подсказку —
    // нет: демон эмитит thread сразу после отпускания hint_lock, то есть через
    // секунды после hint_done ручного ответа (ревью 16.08, №22). Ручной ответ
    // живёт до крестика или до следующей авто-подсказки — та вытесняет.
    private var hintIsManual = false
    // Живой НЕзапрошенный стрим (авто-цикл демона): isHinting его не видит
    // (тот взводится только ручными запросами), а thread-событие посреди
    // авто-стрима резало буфер пополам — хвост рисовался с середины фразы.
    @Published private(set) var isAutoHinting = false
    private var _lastHintUI = Date.distantPast
    // Три независимых пульса: daemon main-thread, сам STT consumer и входные
    // аудиокадры. Раньше первый продолжал слать hb при мёртвом STT, поэтому
    // приложение двадцать минут считало замершую стенограмму здоровой.
    private var lastEventAt = Date()
    private var lastSTTProgressAt: Date?
    private var lastAudioInputAt: Date?
    // Пара часов прошлого тика watchdog: по расхождению стенных и uptime
    // (во сне стоит, как time.monotonic демона) отличаем «проспали» от
    // «зависли» — см. PipelineWatchdog.sleptBetweenTicks.
    private var lastTickWall = Date()
    private var lastTickUptime = ProcessInfo.processInfo.systemUptime
    // Первый stt_progress после сна несёт стенной input_age ≈ длительности
    // сна и надул бы только что перевзведённый якорь обратно (круг 3, GLM).
    private var discardNextInputAge = false
    var watchdog: Timer?
    var userStopped = false
    /// Причина последнего автостопа («silence» | «limit»), пока встреча на экране.
    @Published private(set) var autostopReason: String?
    private var restartAttempts = 0      // защита от краш-лупа: максимум 3 подряд
    /// Потери захвата за одну встречу — отдельный потолок: restartAttempts
    /// обнуляется первой же строкой стенограммы, и цикл «потеря → рестарт →
    /// резервный микрофон → снова потеря» был бы бесконечным (Codex, круг-2
    /// по PR #383). Сбрасывается только ручным стартом.
    var captureLossCount = 0
    static let captureLossLimit = 2
    /// Бюджет потерь исчерпан: встреча закрыта, автоперезапуску здесь не место.
    var captureLossExhausted = false
    /// Причина ближайшего перезапуска из-за потери захвата — чтобы
    /// daemonDied не затирал её общим «Запись прервалась».
    var captureLossReason: String?
    /// Текст сбоя, который обязан пережить остановку с `.preserveFailure`:
    /// демон по пути остановки шлёт свои статусы и затирает его.
    var preservedFailure: String?
    private var lifecycleGate = RecordingLifecycleGate()

    // Gate остаётся закрытым, а подсистема остановки (соседний файл) ходит к
    // нему через эти обёртки. Иначе поле пришлось бы открыть модулю целиком —
    // и любой код смог бы ПЕРЕУСТАНОВИТЬ его (`gate = RecordingLifecycleGate()`),
    // то есть сбросить идущую остановку в idle мимо всех проверок. Struct
    // с mutating-методами закрыть через `private(set)` нельзя: мутация — это
    // запись (ревью 19.08, пятый круг, DeepSeek).
    var stopToken: UUID? { lifecycleGate.token }
    func gateOwns(_ token: UUID, in phase: RecordingLifecycle) -> Bool {
        lifecycleGate.owns(token, in: phase)
    }
    func gateBeginStop() -> UUID? { lifecycleGate.beginStop() }
    func gateFinishStop(_ token: UUID, daemonAlive: Bool) -> Bool {
        lifecycleGate.finishStop(token, daemonAlive: daemonAlive)
    }
    var captureStartTask: Task<Void, Never>?
    var stopFallbackTask: Task<Void, Never>?
    var captureShutdownToken: UUID?
    /// Фаза остановки — подмашина из ShutdownMachine.swift. Раньше здесь был
    /// счётчик ожиданий, а остальное состояние жило в соседних полях и
    /// согласовывалось прозой; теперь переходы проверяются тестами без UI.
    var shutdownPhase: ShutdownPhase = .idle

    enum CleanupDisposition {
        case stopped
        case preserveFailure
        case restart
    }
    var cleanupDisposition: CleanupDisposition = .stopped

    private var suflerRoot: URL { AppSettings.charoiteRoot }

    /// Пока идёт запись, мак не должен уходить в сон по бездействию: человек
    /// на встрече слушает и не трогает клавиатуру — для системы это «простой»,
    /// и после таймаута она усыпляла машину вместе с записью. Симптом ровно
    /// как 20.07: демон жив, PortAudio-стрим молчит. Дисплею гаснуть можно —
    /// звуку экран не нужен; от закрытой крышки это тоже не спасает, и не
    /// должно: закрыл ноутбук — закончил встречу.
    private var sleepGuard: NSObjectProtocol?


    private func beginSleepGuard() {
        guard sleepGuard == nil else { return }
        sleepGuard = ProcessInfo.processInfo.beginActivity(
            options: [.idleSystemSleepDisabled, .userInitiated],
            reason: L.t("Идёт запись встречи", "Meeting recording in progress", "会议录音进行中"))
    }

    func endSleepGuard() {
        if let guardToken = sleepGuard {
            ProcessInfo.processInfo.endActivity(guardToken)
            sleepGuard = nil
        }
    }

    /// Микрофон открывает не приложение, а дочерний python (PortAudio), поэтому
    /// отказ TCC приходил не ошибкой, а тишиной: демон стартовал, статус писал
    /// «Слушаю», индикатор пульсировал, heartbeat шёл — и через час человек
    /// получал пустую стенограмму. Единственный след — трейсбек в логе, куда
    /// никто не смотрит. Спрашиваем до запуска и говорим прямо.
    private func ensureMicrophone(_ then: @escaping (Bool) -> Void) {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            then(true)
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                DispatchQueue.main.async {
                    then(granted)
                }
            }
        default:
            then(false)
        }
    }

    private func micDenied() {
        fail(L.t("Нет доступа к микрофону — Системные настройки › Конфиденциальность › Микрофон",
                 "No microphone access — System Settings › Privacy › Microphone",
                 "无法访问麦克风 — 系统设置 › 隐私与安全性 › 麦克风"))
        if let url = URL(string:
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone") {
            NSWorkspace.shared.open(url)
        }
    }

    func publishLifecycle() {
        lifecycle = lifecycleGate.state
        isRunning = lifecycle == .recording
    }

    func start(preserveUI: Bool = false) {
        // Состояние меняется синхронно до первого permission/await. Поэтому
        // второй клик, URL command или notification не создаст второй capture.
        guard let token = lifecycleGate.beginStart() else { return }
        publishLifecycle()
        userStopped = false
        autostopReason = nil
        pipelineHealth = PipelineHealthMonitor()
        // Автостоп извещает баннером того, кого нет у экрана. Разрешение
        // раньше запрашивал только календарь — у всех остальных единственный
        // канал оповещения молча не существовал (ревью 18.08 ×2).
        MeetingNotificationService.shared.requestAuthorization()
        status = L.t("Запускаю…", "Starting…", "启动中…")
        statusIsError = false
        statusErrorFromDaemon = false

        // Статус TCC проверяем на каждый Start. Кэшировать сам факт проверки
        // нельзя: после отказа второе нажатие раньше запускало демон в тишине.
        ensureMicrophone { [weak self] granted in
            guard let self, self.lifecycleGate.owns(token, in: .starting) else { return }
            guard granted else {
                self.micDenied()
                self.beginFailedStartCleanup(token: token)
                return
            }
            self.continueStart(token: token, preserveUI: preserveUI)
        }
    }

    /// Двухшаговый стоп короткой записи. Константа tooShortToStop годами
    /// описывала защиту от промаха по кнопке, но нигде не применялась
    /// (ревью 15.08 ×2): случайный клик в первые секунды молча убивал
    /// запись. Первый «Стоп» на короткой записи взводит подтверждение и
    /// говорит об этом строкой статуса; второй в течение пяти секунд —
    /// останавливает. Программный stop() защиту не проходит — она про
    /// кнопки, не про автоматизацию.
    private var stopArmedAt: Date?
    /// Взведение видно экранам, а не только строке статуса суфлёра: человек,
    /// нажавший «Стоп» на «Сегодня», должен увидеть подтверждение там же
    /// (ревью 15.08 ×4).
    @Published private(set) var stopConfirmPending = false

    func toggle() {
        switch lifecycle {
        case .idle:
            stopArmedAt = nil
            stopConfirmPending = false
            start()
        case .recording:
            let elapsed = recordingStartedAt.map { Date().timeIntervalSince($0) } ?? 0
            if elapsed < Self.tooShortToStop {
                let armed = stopArmedAt.map { Date().timeIntervalSince($0) <= 5 } ?? false
                if !armed {
                    stopArmedAt = Date()
                    stopConfirmPending = true
                    status = L.t("Запись только началась — «Стоп» ещё раз, чтобы точно остановить",
                                 "Recording just started — press Stop again to confirm",
                                 "录音刚开始——再按一次停止以确认")
                    Task { [weak self] in   // взведение гаснет само через 5 с
                        try? await Task.sleep(nanoseconds: 5_200_000_000)
                        await MainActor.run { [weak self] in
                            guard let self, let at = self.stopArmedAt,
                                  Date().timeIntervalSince(at) > 5 else { return }
                            self.stopConfirmPending = false
                        }
                    }
                    return
                }
            }
            stopArmedAt = nil
            stopConfirmPending = false
            stop()
        case .starting, .stopping:
            break
        }
    }

    private func continueStart(token: UUID, preserveUI: Bool) {
        guard lifecycleGate.owns(token, in: .starting) else { return }
        // При корректной state machine idle никогда не сосуществует с живым
        // дочерним процессом. Guard оставляем как fail-closed страховку после
        // обновления со старой версии или будущей регрессии.
        guard process?.isRunning != true else {
            fail(L.t("Предыдущая встреча ещё завершается — попробуйте снова через несколько секунд",
                     "The previous meeting is still finishing — try again in a few seconds",
                     "上一场会议仍在收尾——请几秒后重试"))
            beginFailedStartCleanup(token: token)
            return
        }
        if !preserveUI {                 // авто-рестарт не должен стирать встречу с экрана
            lines = []
            hint = ""
            cloud = ""
            // Нить прошлой встречи держится на экране до старта следующей —
            // после «Стоп» её дочитывают и копируют. Но в новую встречу она
            // ехать не должна: демон пришлёт нить не сразу, и первые минуты
            // человек читал бы вчерашний разговор как сегодняшний.
            thread = ""
            // Ручной старт — это новая попытка, а не продолжение краш-лупа.
            // Без сброса одна неудачная серия (например, осиротевший python
            // держал flock) навсегда выключала автовосстановление: человек жал
            // «Слушать встречу» и мгновенно получал то же ⛔️ без объяснения.
            restartAttempts = 0
            captureLossCount = 0
            captureLossReason = nil
            captureLossExhausted = false
            preservedFailure = nil
        }
        _hintBuf = ""; _lastHintUI = .distantPast
        hintIsManual = false
        // демон мог умереть посреди генерации — hint_done уже не придёт,
        // без сброса кнопки Подсказка/Claude/Протокол залипали заблокированными
        isHinting = false
        isAutoHinting = false
        isClouding = false

        // 🔴 ВЫКЛЮЧЕНО 06.08 по итогам боевого теста. Тап создаётся, виден
        // системе и из отдельного процесса отдаёт звук, но демон не получает
        // от него ни кадра (0 байт за 94 секунды записи). Диагноз «-10851 от
        // размера блока» опровергнут: сообщение лестницы о другой ступени не
        // появилось — поток открылся штатно и просто молчал. Страховка
        // конвейера (PR #249) отработала: встреча записана микрофоном.
        // Следующая гипотеза — двум входным потокам тесно в одном процессе;
        // проверять только не на рабочей машине: осиротевший агрегат дважды
        // за день подвесил CoreAudio. Включать после разбора.
        // 🔴 ВЫКЛЮЧЕН 07.08 после четвёртого подвеса звука на рабочей машине.
        // Схема «приложение читает тап и стримит демону» работает — 38.9 с
        // системного звука записаны в бою. Но сам цикл создания и уничтожения
        // тап-агрегата клинит coreaudiod на macOS 26.5: после встречи динамики
        // умолкают, лечится только SIGKILL демону звука с правами админа.
        // Цена неприемлема: человек теряет звук машины после каждой встречи.
        // Включать обратно — на macOS 27 или после того, как найдётся, что
        // именно в цикле клинит CoreAudio. До тех пор системный звук берём
        // проверенным BlackHole.
        // startSystemAudioTap()

        // Системный звук берём ScreenCaptureKit: агрегатных устройств он не
        // создаёт, поэтому подвесить CoreAudio не может — в отличие от тапов,
        // стоивших четырёх подвесов 06–07.08. Захват поднимаем ДО демона:
        // манифест обязан существовать к моменту, когда python выбирает
        // источники, иначе встреча уйдёт на BlackHole.
        if #available(macOS 13.0, *), systemAudioCapture == nil {
            SystemAudioCapture.captureLog("старт записи: поднимаю ScreenCaptureKit")
            let capture = SystemAudioCapture()
            // Поток ScreenCaptureKit умер посреди встречи и не пересоздался
            // (карточка №35): человек должен узнать сразу, а не из пустой
            // стенограммы — на macOS 15 с ним уходит и микрофон.
            capture.onCaptureLost = { [weak self] reason, userStopped in
                self?.captureLost(reason: reason, userStopped: userStopped)
            }
            capture.onCaptureRecovered = { [weak self] _ in self?.captureRecovered() }
            systemAudioCapture = capture
            let task = Task { @MainActor [weak self] in
                let ready = await capture.start()
                guard let self else {
                    await capture.stop()
                    return
                }
                self.captureStartTask = nil
                // Stop мог прийти, пока ScreenCaptureKit ждал первые кадры.
                // Устаревший completion не имеет права запускать daemon.
                guard self.lifecycleGate.owns(token, in: .starting) else { return }
                if !ready {
                    self.systemAudioCapture = nil
                    // Фолбэк — вслух: молча уходить на BlackHole нельзя (№140).
                    self.announceCaptureFallback()
                }
                SystemAudioCapture.captureLog(ready
                    ? "захват готов — демон стартует с манифестом"
                    : "захват НЕ поднялся — демон уйдёт на BlackHole")
                self.launchDaemon(preserveUI: preserveUI, token: token)
            }
            captureStartTask = task
            return
        }
        // Блок старта пропущен: capture остался от прошлой встречи — главный
        // подозреваемый тихого фолбэка №140. Молча уходить в демона нельзя.
        SystemAudioCapture.captureLog("старт записи БЕЗ нового захвата: systemAudioCapture уже занят "
            + "(isActive=\((systemAudioCapture as? SystemAudioCapture)?.isActive ?? false))")
        launchDaemon(preserveUI: preserveUI, token: token)
    }

    /// Запуск python-демона. Отделён от start(), потому что захват звука
    /// поднимается асинхронно, а демон обязан стартовать ПОСЛЕ него.
    private func launchDaemon(preserveUI: Bool, token: UUID) {
        guard lifecycleGate.owns(token, in: .starting) else { return }
        let p = Process()
        // Код — из бандла (если он там), данные — в рабочую папку человека:
        // бандл подписан и доступен только на чтение. Обещание держится
        // кодом с 16.08: `codeRoot` больше не подхватывает записываемую
        // копию молча — иначе процесс без прав на микрофон подкладывал бы
        // свой daemon.py и исполнялся с нашими (см. AppSettings.codeRoot).
        p.arguments = ["src/daemon.py"]
        AppSettings.preparePython(p, root: suflerRoot)

        let outPipe = Pipe()
        let inPipe = Pipe()
        p.standardOutput = outPipe
        p.standardInput = inPipe
        // stderr — в лог: без него сбои демона невидимы (logs/daemon.err.log)
        let logDir = suflerRoot.appendingPathComponent("logs")
        FileManager.default.createPrivateDirectory(at: logDir)  // логи демона несут куски стенограмм
        let errURL = logDir.appendingPathComponent("daemon.err.log")
        // append, не пересоздание: авто-рестарт после крэша затирал трейсбек
        // ровно в момент, когда он нужен для диагноза
        if !FileManager.default.fileExists(atPath: errURL.path) {
            FileManager.default.createPrivateFile(atPath: errURL.path)   // 0600 сразу, не по umask
        }
        FileManager.default.makePrivate(atPath: errURL.path)   // лог старой установки
        LogTrim.trim(errURL)   // потолок: хвост остаётся, гигабайты — нет
        let errFH = try? FileHandle(forWritingTo: errURL)
        errFH?.seekToEndOfFile()
        p.standardError = errFH ?? FileHandle.nullDevice
        errHandle = errFH

        outPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else {
                // EOF: без снятия хендлера dispatch-source жил после смерти демона —
                // CPU-спин пустыми срабатываниями + утечка pipe на каждый рестарт
                handle.readabilityHandler = nil
                return
            }
            Task { @MainActor [weak self] in self?.consume(data) }
        }
        stdoutHandle = outPipe.fileHandleForReading
        p.terminationHandler = { [weak self] proc in
            Task { @MainActor [weak self] in self?.daemonDied(proc) }
        }

        do {
            try p.run()
            process = p
            stdinPipe = inPipe
            guard lifecycleGate.markRecording(token) else {
                p.terminate()
                return
            }
            publishLifecycle()
            beginSleepGuard()
            startClock()
            // Сохранённые дефолты — новому демону (stdin буферизуется до
            // готовности). Метка quiet: это синхронизация, не живой клик —
            // без неё каждый авто-рестарт демона рождал «⚙️ … выключены» и
            // затирал строку «Запись прервалась — восстанавливаю» (круги
            // 1-2 по #394). Статус остаётся только у переключений человеком.
            for (key, on) in [("hints", hintsOn), ("cloud", cloudOn)] where !on {
                send("set \(key) off quiet")
            }
            // Тезисный контур убран целиком (пакет владельца 24.08, свойство
            // thesesOn удалено партией G-П1 карты #402): включить слой некому
            // — глушим безусловно; посылка НЕСУЩАЯ, демон иначе поднимет
            // слой дефолтом.
            send("set theses off quiet")
            lastEventAt = Date()
            lastSTTProgressAt = nil
            lastAudioInputAt = nil
            pipelineHealth = PipelineHealthMonitor()
            lastTickWall = Date()
            lastTickUptime = ProcessInfo.processInfo.systemUptime
            watchdog?.invalidate()
            watchdog = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
                Task { @MainActor [weak self] in self?.checkAlive() }
            }
        } catch {
            fail(L.t("Не удалось начать запись: \(error.localizedDescription)", "Could not start recording: \(error.localizedDescription)", "无法开始录音：\(error.localizedDescription)"))
            beginFailedStartCleanup(token: token)
        }
    }

    /// Поднять системный звук через Core Audio tap.
    ///
    /// До macOS 14.4 API нет, разрешение может быть не выдано, устройство
    /// вывода бывает недоступно — во всех случаях просто работаем по-старому
    /// через BlackHole. Молчать об этом нельзя только в одном месте: если нет
    /// НИ тапа, НИ драйвера, демон запишет одну свою сторону разговора.
    private func startSystemAudioTap() {
        guard #available(macOS 14.4, *) else { return }
        let tap = (systemAudioTap as? SystemAudioTap) ?? SystemAudioTap()
        systemAudioTap = tap
        if tap.start() == nil { systemAudioTap = nil }
    }

    /// Демон-процесс умер (крэш или наш terminate). Если это не ручной Стоп —
    /// поднимаем свежий, не стирая встречу с экрана (стенограмма-файл цел).
    private func daemonDied(_ proc: Process) {
        guard proc === process else { return }  // умер прошлый демон, не текущий
        process = nil
        stdoutHandle?.readabilityHandler = nil
        stdoutHandle = nil
        try? errHandle?.close()
        errHandle = nil
        let wasRecording = lifecycle == .recording
        stopClock()
        isHinting = false   // ждать hint_done/cloud_done от мёртвого демона бессмысленно
        disarmHintTimeout()
        isClouding = false
        watchdog?.invalidate()
        watchdog = nil
        lastSTTProgressAt = nil
        lastAudioInputAt = nil
        pipelineHealth = PipelineHealthMonitor()

        // Штатный Stop уже перевёл gate в stopping. Только теперь, после
        // фактической смерти читателя, можно закрывать capture и разрешать
        // следующую встречу.
        if lifecycle == .stopping, let token = lifecycleGate.token {
            // Через машину: `daemonExited` было объявлено и оттестировано,
            // но сервис его не слал — ровно тот же класс дефекта, что с
            // `killTimeout` кругом раньше (ревью 19.08).
            applyShutdown(.daemonExited, token: token)
            return
        }

        // Статусы читает человек на встрече, а не разработчик в логах. «Демон
        // умер», «нет heartbeat» ему ничего не говорят — важно другое: пишется
        // ли встреча прямо сейчас и надо ли что-то делать руками.
        switch Self.restartDecision(wasRecording: wasRecording,
                                    userStopped: userStopped,
                                    attempts: restartAttempts) {
        case .none:
            endSleepGuard()   // записи больше нет — маку можно спать
            status = Self.stoppedStatus(autostopReason: autostopReason)
            statusIsError = false
            return
        case .giveUp:
            if let reason = captureLossReason {
                captureLossReason = nil
                endSleepGuard()
                fail(L.t("⛔️ Захват звука потерян (\(reason)) и не восстановился. Нажмите «Слушать встречу» ещё раз",
                         "⛔️ Audio capture lost (\(reason)) and did not recover. Press \u{201C}Listen to the meeting\u{201D} again",
                         "⛔️ 音频捕获已丢失（\(reason)）且未能恢复。请再次点击「旁听会议」"))
                guard let token = lifecycleGate.beginStop() else { return }
                cleanupDisposition = .preserveFailure
                publishLifecycle()
                beginCaptureShutdown(token: token)
                return
            }
            // Три попытки подряд не помогли — молчать нельзя: человек уверен,
            // что встреча пишется, а запись давно встала. Страж сна тоже
            // снимаем: иначе провалившаяся запись навсегда запрещала маку
            // спать — до перезапуска приложения.
            endSleepGuard()
            fail(L.t("⛔️ Запись остановилась и не восстановилась. Нажмите «Слушать встречу» ещё раз",
                     "⛔️ Recording stopped and did not recover. Press \u{201C}Listen to the meeting\u{201D} again",
                     "⛔️ 录音已停止且未能恢复。请再次点击「旁听会议」"))
            guard let token = lifecycleGate.beginStop() else { return }
            cleanupDisposition = .preserveFailure
            publishLifecycle()
            beginCaptureShutdown(token: token)
            return
        case .restart:
            break
        }
        restartAttempts += 1
        if let reason = captureLossReason {
            captureLossReason = nil
            fail(L.t("Захват звука потерян (\(reason)) — восстанавливаю запись (\(restartAttempts) из 3)",
                     "Audio capture lost (\(reason)) — recovering the recording (\(restartAttempts) of 3)",
                     "音频捕获已丢失（\(reason)）——正在恢复录音（\(restartAttempts)/3）"))
        } else {
            fail(L.t("Запись прервалась — восстанавливаю (\(restartAttempts) из 3)", "Recording dropped — recovering (\(restartAttempts) of 3)", "录音中断——恢复中（第 \(restartAttempts)/3 次）"))
        }
        guard let token = lifecycleGate.beginStop() else { return }
        cleanupDisposition = .restart
        publishLifecycle()
        beginCaptureShutdown(token: token)
    }

    /// Процесс жив, но один из обязательных контуров молчит 100с.
    /// Главный hb, STT и аудиовход проверяются отдельно: живой daemon больше
    /// не может прикрыть умершее распознавание зелёным heartbeat.
    private func checkAlive() {
        guard isRunning, let p = process, p.isRunning else { return }
        let now = Date()
        let uptime = ProcessInfo.processInfo.systemUptime
        let wallDelta = now.timeIntervalSince(lastTickWall)
        let uptimeDelta = uptime - lastTickUptime
        lastTickWall = now
        lastTickUptime = uptime
        if PipelineWatchdog.sleptBetweenTicks(wallDelta: wallDelta,
                                              uptimeDelta: uptimeDelta) {
            // Мак спал: все якоря — стенные, после пробуждения каждый из них
            // равен длительности сна, и здоровый демон улетел бы в рестарт
            // (три сна подряд — в giveUp). Перевзводим якоря и даём демону
            // один тик на первый живой hb (ревью 21.08, DeepSeek).
            lastEventAt = now
            if lastSTTProgressAt != nil { lastSTTProgressAt = now }
            if lastAudioInputAt != nil { lastAudioInputAt = now }
            discardNextInputAge = true
            return
        }
        let daemonAge = now.timeIntervalSince(lastEventAt)
        let sttAge = lastSTTProgressAt.map { now.timeIntervalSince($0) }
        var audioAge = lastAudioInputAt.map { now.timeIntervalSince($0) }
        if #available(macOS 13.0, *),
           (systemAudioCapture as? SystemAudioCapture)?.isRestarting == true {
            // Источник пересоздаётся после сбоя ScreenCaptureKit: демон жив,
            // тишина ожидаема, и перезапуск встречи только оборвал бы цикл
            // (круг-1 по PR #383, DeepSeek). Сдастся — сам перезапустит.
            audioAge = nil
        }
        if captureLossExhausted { return }   // бюджет потерь исчерпан, встреча закрыта
        guard PipelineWatchdog.shouldRestart(
            daemonEventAge: daemonAge,
            sttProgressAge: sttAge,
            audioInputAge: audioAge
        ) else { return }
        // Порядок веток: полный молчок демона — первым. sttAge всегда не
        // меньше daemonAge, и прежний порядок при зависании всего процесса
        // валил вину на распознавание (ревью 21.08, Gemini).
        if daemonAge > PipelineWatchdog.timeout {
            fail(L.t("Запись замерла — перезапускаю",
                     "Recording stalled — restarting",
                     "录音停滞——正在重启"))
        } else if let sttAge, sttAge > PipelineWatchdog.timeout {
            fail(L.t("Распознавание замерло — перезапускаю запись",
                     "Transcription stalled — restarting recording",
                     "转写已停滞——正在重启录音"))
        } else {
            fail(L.t("Аудиопоток замер — перезапускаю запись",
                     "Audio input stalled — restarting recording",
                     "音频输入已停滞——正在重启录音"))
        }
        p.terminate()
        DispatchQueue.global().asyncAfter(deadline: .now() + 5.0) {
            if p.isRunning { kill(p.processIdentifier, SIGKILL) }  // SIGTERM дедлок не берёт
        }
    }

    /// Предохранитель на генерацию.
    ///
    /// isHinting снимался ТОЛЬКО событием hint_done от демона. Если Ollama
    /// перезагружала модель или вставала, поток генерации висел на HTTP —
    /// hint_done не приходил никогда, а главный цикл демона продолжал слать
    /// hb, поэтому ни watchdog, ни daemonDied не срабатывали. Кнопки
    /// «Подсказка» и «Протокол» оставались заблокированными до конца встречи,
    /// и спросить было нельзя ровно тогда, когда это нужнее всего.
    private var hintDeadline: Timer?
    private var _lastHintRearm = Date.distantPast

    private func armHintTimeout() {
        hintDeadline?.invalidate()
        hintDeadline = Timer.scheduledTimer(withTimeInterval: 150, repeats: false) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, self.isHinting else { return }
                self.isHinting = false
                self.fail(L.t("Модель не ответила — попробуйте ещё раз",
                              "The model did not answer — try again",
                              "模型没有响应——请重试"))
            }
        }
    }

    private func disarmHintTimeout() {
        hintDeadline?.invalidate()
        hintDeadline = nil
    }

    func requestHint() {
        guard isRunning, !isHinting else { return }
        hint = ""
        _hintBuf = ""; _lastHintUI = .distantPast
        isHinting = true
        hintIsManual = true
        armHintTimeout()
        send("hint")
    }

    func requestSummary() {
        guard isRunning, !isHinting else { return }
        hint = ""
        _hintBuf = ""; _lastHintUI = .distantPast
        isHinting = true
        hintIsManual = true
        armHintTimeout()
        send("summary")
    }

    /// Крестик на карточке подсказки: ручной ответ нить не гасит (он может
    /// быть нужен до конца встречи), поэтому убрать его с экрана может
    /// только сам человек — или вытеснить следующая авто-подсказка.
    func dismissHint() {
        // посреди ЛЮБОГО живого стрима (ручного или авто) токены через ≤33мс
        // воскресят карточку — крестик в UI в это время спрятан, guard —
        // оборона от гонки «стрим начался между кадром и кликом»
        guard !isHinting, !isAutoHinting else { return }
        hint = ""
        _hintBuf = ""
        hintIsManual = false
    }

    /// Гасит ли обновление нити карточку подсказки. Чистая функция — обе
    /// критические ошибки ревью 16.08 (стирание ручного ответа, ампутация
    /// идущего авто-стрима) прошли бы мимо тестов, живи решение в consume.
    /// Гаснет только ЗАВЕРШЁННЫЙ авто-контент (бриф, старая авто-подсказка);
    /// живые стримы и ручной ответ нить не трогает.
    nonisolated static func threadClearsHint(isHinting: Bool, isAutoHinting: Bool,
                                             hintIsManual: Bool) -> Bool {
        !isHinting && !isAutoHinting && !hintIsManual
    }

    /// ⏮: хвосты прошлых встреч по текущей теме нити — из графа в нить.
    /// Не занимает панель подсказки: ответ дописывается в нить строками ⏮,
    /// поэтому isHinting не трогаем — подсказку можно просить параллельно.
    /// Состояние ставим до отправки команды: два быстрых клика не успеют
    /// положить в очередь две одинаковые генерации до ответа демона.

    func requestCloud() {
        // cloudOn — единственный тумблер, отправляющий стенограмму с машины.
        // Выключен — не шлём даже по горячей клавише: ⌘⇧⏎ работает и тогда,
        // когда кнопка серая, а демон до правки принимал команду `cloud`ом
        // не глядя на выключатель.
        guard isRunning, !isClouding, cloudOn else { return }
        send("cloud")
    }

    func ask(_ question: String) {
        let q = question.trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "\n", with: " ")
        // !isHinting: вопрос поверх идущей генерации мешал токены двух ответов в панели
        guard isRunning, !isHinting, !q.isEmpty else { return }
        hint = ""
        _hintBuf = ""; _lastHintUI = .distantPast
        isHinting = true
        hintIsManual = true
        // тот же предохранитель, что у «Подсказки»/«Протокола»: без него зависший
        // ответ держал поле «Спросить» и кнопки отключёнными до конца встречи
        armHintTimeout()
        send("ask " + q)
    }

    /// Команда демону в stdin. Открыта модулю только потому, что `stop()`
    /// живёт в соседнем файле: снаружи сервиса звать её нельзя — она минует
    /// и lifecycle-гейт, и подтверждение остановки короткой записи.
    func send(_ cmd: String) {
        guard let fh = stdinPipe?.fileHandleForWriting,
              let data = (cmd + "\n").data(using: .utf8) else { return }
        try? fh.write(contentsOf: data)
    }

    private func noteSTTProgress(_ obj: [String: Any]) {
        let now = Date()
        // Сам факт события доказывает жизнь STT даже при несовместимом новом
        // поле телеметрии. Декодер снимка не вправе разоружить watchdog.
        lastSTTProgressAt = now
        var nextHealth = pipelineHealth
        if nextHealth.acceptProgress(obj) != nil {
            pipelineHealth = nextHealth
        }
        if discardNextInputAge {
            // тик сна уже перевзвёл якорь; стенной возраст этого события —
            // эхо сна, не смерть входа. Один пропуск: следующий stt_progress
            // принесёт честный возраст.
            discardNextInputAge = false
            lastAudioInputAt = now
        } else if let age = (obj["input_age_seconds"] as? NSNumber)?.doubleValue {
            // Python присылает возраст последнего кадра, а не свои настенные
            // часы: смена часового пояса не превращается в ложное зависание.
            lastAudioInputAt = now.addingTimeInterval(-max(0, age))
        }
    }

    private func noteHeartbeat(_ obj: [String: Any]) {
        // Главный поток видит зависший native inference, но не является
        // прогрессом STT: lastSTTProgressAt здесь намеренно не двигается.
        var nextHealth = pipelineHealth
        if nextHealth.acceptHeartbeat(obj) != nil {
            pipelineHealth = nextHealth
        }
    }

    private func consume(_ data: Data) {
        stdoutBuffer.append(data)
        lastEventAt = Date()  // любое событие (включая hb) = демон жив
        while let nl = stdoutBuffer.firstIndex(of: 0x0A) {
            let lineData = stdoutBuffer.prefix(upTo: nl)
            stdoutBuffer.removeSubrange(...nl)
            guard let obj = try? JSONSerialization.jsonObject(with: lineData) as? [String: Any],
                  let type = obj["type"] as? String else { continue }
            let text = obj["text"] as? String ?? ""
            switch type {
            case "stt_progress":
                noteSTTProgress(obj)
            case "hb":
                noteHeartbeat(obj)
            case "status":
                status = text
                // Признак сбоя приходит от демона (`error: true`), обычный статус
                // его снимает: раньше флаг был липким — после одного таймаута
                // все дальнейшие «⚡ отвечаю» и «минутки обновлены» шли красным
                statusIsError = obj["error"] as? Bool ?? false
                statusErrorFromDaemon = statusIsError
            case "transcript":
                let spk = obj["speaker"] as? String ?? ""
                let plain = obj["plain"] as? String ?? ""
                // куски одного голоса — в один абзац, а не строкой-«батчем» на каждый
                // 3с-чанк; новый блок только на смене говорящего (или очень длинном)
                if !spk.isEmpty, !plain.isEmpty, let last = lines.last,
                   last.speaker == spk, last.text.count < 2500 {
                    lines[lines.count - 1].text += " " + plain
                } else {
                    lines.append(TranscriptLine(ts: obj["ts"] as? String ?? "",
                                                speaker: spk,
                                                text: plain.isEmpty ? text : plain))
                }
                if lines.count > 500 { lines.removeFirst(lines.count - 500) }
                restartAttempts = 0  // транскрипция реально идёт — лимит рестартов обнуляем
            case "thread":
                thread = text
                if Self.threadClearsHint(isHinting: isHinting,
                                         isAutoHinting: isAutoHinting,
                                         hintIsManual: hintIsManual) {
                    // буфер чистим вместе с hint: иначе первый токен следующей
                    // генерации воскрешал стёртое (ревью 16.08, №22)
                    hint = ""; _hintBuf = ""
                }
            case "autostop":
                // Человек мог нажать «Стоп» за мгновение до события: тогда это
                // его остановка, а не наша — ни баннера, ни чужого статуса.
                guard lifecycle == .recording else { break }
                // Демон решил, что запись пора закончить (тишина или потолок
                // длительности), и ЖДЁТ нашей команды: сам он не выходит —
                // иначе смерть демона выглядела бы крахом записи и мы подняли
                // бы новую встречу поверх забытой. Останавливаем штатно, тем
                // же путём, что кнопка «Стоп».
                let reason = obj["reason"] as? String ?? "silence"
                autostopReason = reason
                MeetingNotificationService.shared.presentAutostop(reason: reason, detail: text)
                stop()
                status = reason == "limit"
                    ? L.t("⏹ Запись остановлена: \(text)",
                          "⏹ Recording stopped: \(text)",
                          "⏹ 录音已停止：\(text)")
                    : L.t("⏹ Запись остановлена автоматически: \(text)",
                          "⏹ Recording stopped automatically: \(text)",
                          "⏹ 录音已自动停止：\(text)")
            case "autostop_warning":
                // Предупреждение перед автостопом: любая речь его снимает, и
                // тогда демон пришлёт обычный статус «автостоп отменён».
                // Строку статуса перебивают соседние контуры («⚡ отвечаю»,
                // «Минутки…»), а минута на ответ — короткая: когда окна не
                // видно, шлём ещё и баннер (ревью 18.08, GLM).
                status = text
                MeetingNotificationService.shared.presentAutostopWarning(text)
            case "hint":
                // Демон помечает стрим: manual — запрошен человеком, иначе
                // авто-цикл. Старый демон поля не шлёт — тогда считаем токен
                // ручным, пока ждём ручной стрим, и авто — когда не ждём.
                let manual = obj["manual"] as? Bool ?? isHinting
                if !manual && isHinting {
                    // хвост уступающего авто-стрима («…⏸»), пока ждём ручной
                    // ответ: раньше вклеивался в его начало — теперь мимо
                    // карточки (демон сам пишет его в лог подсказок)
                    isAutoHinting = true  // его hint_done ещё придёт
                    break
                }
                if !manual && !isAutoHinting {
                    // первый токен нового авто-стрима: карточка уступает —
                    // прежний контент (бриф или прочитанный ручной ответ)
                    // вытесняется свежей подсказкой, а не копится лентой
                    isAutoHinting = true
                    hintIsManual = false
                    _hintBuf = ""; _lastHintUI = .distantPast
                }
                // троттл ~30fps: hint растёт по токену, растущий Text = O(n²)
                _hintBuf += text
                if Date().timeIntervalSince(_lastHintUI) >= 0.033 {
                    hint = _hintBuf; _lastHintUI = Date()
                }
                // Идущая генерация — не зависшая: пока токены приходят, дедлайн
                // отодвигается. Иначе медленный протокол на длинной встрече
                // ловил «Модель не ответила» на 150-й секунде, а токены потом
                // сыпались поверх ошибки (аудит 18.08).
                if manual && isHinting && hintDeadline != nil,
                   Date().timeIntervalSince(_lastHintRearm) >= 5 {
                    _lastHintRearm = Date()
                    armHintTimeout()
                }
            case "transcript_markup":
                // e4b разметил реплики диалога внутри последнего абзаца этого голоса
                let spk = obj["speaker"] as? String ?? ""
                if !spk.isEmpty, !text.isEmpty {
                    for i in stride(from: lines.count - 1, through: 0, by: -1)
                    where lines[i].speaker == spk {
                        lines[i].text = text
                        break
                    }
                }
            case "hint_done":
                let manual = obj["manual"] as? Bool ?? isHinting
                if !manual {
                    isAutoHinting = false
                    // done уступившего авто, пока ждём ручной ответ: не
                    // флашить чужой хвост и не сбрасывать ручной isHinting
                    if isHinting { break }
                }
                hint = _hintBuf  // финальный флаш хвоста
                isHinting = false
                disarmHintTimeout()
            case "cloud_start":
                // лента, как hint: `cloud = …` затирала все прошлые ответы Haiku
                cloud += (cloud.isEmpty ? "" : "\n\n") + text + "\n"
                isClouding = true
            case "cloud":
                cloud += text
                if cloud.count > 40_000 {  // панель не должна пухнуть бесконечно
                    cloud = String(cloud.suffix(30_000))
                }
            case "cloud_done":
                isClouding = false
            case "rename":
                // нейросеть надёжно определила имя: «Собеседник N» → имя,
                // задним числом по всей ленте (стенограмму демон правит сам)
                if let from = obj["from"] as? String,
                   let to = obj["to"] as? String, !from.isEmpty, !to.isEmpty {
                    for i in lines.indices where lines[i].speaker == from {
                        lines[i].speaker = to
                    }
                }
            default:
                break  // hb и будущие типы — просто отметка живости выше
            }
        }
    }
}

#endif
