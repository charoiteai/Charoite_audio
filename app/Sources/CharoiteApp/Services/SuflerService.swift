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

    @Published var isRunning = false

    /// Когда началась текущая запись. Не nil ровно тогда, когда идёт встреча.
    ///
    /// «Идёт запись» без времени — обещание без доказательства: человек,
    /// который вернулся к ноутбуку через час, не может отличить работающую
    /// запись от зависшего индикатора. Часы отсчитывают от старта демона,
    /// а не от первой реплики: тишина в начале встречи тоже записана.
    @Published private(set) var recordingStartedAt: Date?

    /// Тик раз в секунду, чтобы SwiftUI перерисовывал таймер.
    @Published private(set) var recordingElapsed: TimeInterval = 0
    @Published var status = L.t("Готов к запуску", "Ready", "就绪")
    /// Текущий статус — про сбой, а не про обычный ход дела.
    ///
    /// Раньше это определялось поиском подстрок в самом статусе («прервалась»,
    /// «Не удалось»), а статусы локализованы: у англо- и китаеязычного
    /// пользователя сообщение об оборванной записи выводилось мелким серым
    /// текстом в одну строку и обрезалось. Признак должен приходить из
    /// модели, а не угадываться по переводу.
    @Published var statusIsError = false

    /// Ставит статус и помечает его как сообщение об отказе.
    private func fail(_ text: String) {
        status = text
        statusIsError = true
    }
    @Published var lines: [TranscriptLine] = []
    @Published var theses: [String] = []
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
    @Published private(set) var isExpanding = false
    @Published var cloud = ""          // ответ Claude (Sonnet) — третья панель
    @Published var isClouding = false

    // Живые тумблеры контуров. Выбор запоминается и становится дефолтом
    // следующих встреч (UserDefaults) — «один раз отказался, и так дальше».
    @Published var hintsOn = UserDefaults.standard.object(forKey: "sufler.hints") as? Bool ?? true {
        didSet { applyToggle("hints", hintsOn) }
    }
    @Published var thesesOn = UserDefaults.standard.object(forKey: "sufler.theses") as? Bool ?? true {
        didSet { applyToggle("theses", thesesOn) }
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

    /// Часы записи. Секундный тик — единственное, что здесь происходит:
    /// длительность считается от даты старта, а не накоплением тиков, поэтому
    /// уснувший ноутбук её не «съедает».
    private func startClock() {
        let started = Date()
        recordingStartedAt = started
        recordingElapsed = 0
        clock?.invalidate()
        clock = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, let from = self.recordingStartedAt else { return }
                self.recordingElapsed = Date().timeIntervalSince(from)
            }
        }
    }

    private func stopClock() {
        clock?.invalidate()
        clock = nil
        recordingStartedAt = nil
        recordingElapsed = 0
    }

    /// «18:42» — мм:сс, а после часа «1:18:42». Для таймера, который человек
    /// читает боковым зрением, ведущие нули у минут важнее единообразия.
    nonisolated static func clockText(_ seconds: TimeInterval) -> String {
        let total = max(0, Int(seconds))
        let (h, m, s) = (total / 3600, (total % 3600) / 60, total % 60)
        return h > 0
            ? String(format: "%d:%02d:%02d", h, m, s)
            : String(format: "%d:%02d", m, s)
    }

    /// Слишком короткая запись — скорее всего промах по кнопке.
    nonisolated static let tooShortToStop: TimeInterval = 20

    private var process: Process?
    private var stdinPipe: Pipe?
    private var stdoutHandle: FileHandle?  // для снятия readabilityHandler при смерти демона
    private var errHandle: FileHandle?     // daemon.err.log — закрывать, иначе fd-утечка на рестартах
    /// Системный звук без BlackHole. Живёт ровно столько же, сколько демон:
    /// поднимается перед стартом, гасится в stop() и при смерти демона —
    /// иначе устройство останется висеть в системе.
    private var systemAudioTap: Any?
    /// Захват ScreenCaptureKit — основной путь к системному звуку с 07.08.
    /// Живёт столько же, сколько демон: поднимается перед стартом, гаснет
    /// в stop() и при смерти демона.
    private var systemAudioCapture: Any?
    private var stdoutBuffer = Data()
    private var _hintBuf = ""            // буфер троттла подсказки (см. consume)
    private var _lastHintUI = Date.distantPast
    // Watchdog: демон шлёт hb каждые 30с из главного цикла; тишина 100с на живом
    // процессе = завис (20.07: встреча шла, транскрипция молча стояла 20 минут)
    private var lastEventAt = Date()
    private var watchdog: Timer?
    private var clock: Timer?
    private var userStopped = false
    private var restartAttempts = 0      // защита от краш-лупа: максимум 3 подряд
    private var micChecked = false       // разрешение спрашиваем один раз за сессию

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
            reason: "Идёт запись встречи")
    }

    private func endSleepGuard() {
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
    private func ensureMicrophone(_ then: @escaping () -> Void) {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            then()
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                DispatchQueue.main.async {
                    if granted { then() } else { self.micDenied() }
                }
            }
        default:
            micDenied()
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

    func start(preserveUI: Bool = false) {
        guard !isRunning else { return }
        guard micChecked else {
            micChecked = true
            ensureMicrophone { [weak self] in self?.start(preserveUI: preserveUI) }
            return
        }
        // Прошлый демон ещё дожёвывает stop (грейс 8-12с) и держит flock.
        // Раньше здесь стоял немедленный SIGKILL — и он попадал в окно ~0.5с
        // между командой «стоп» и запуском пересборки: демон не успевал
        // стартовать rebuild_transcript и финализировать .pcm → .wav, то есть
        // предыдущая встреча теряла и финальную стенограмму, и граф. Двойной
        // клик по кнопке этого стоить не должен: ждём штатной смерти, добиваем
        // только по таймауту.
        if let old = process, old.isRunning {
            status = L.t("Дописываю прошлую встречу…",
                         "Finishing previous meeting…",
                         "正在收尾上一场会议…")
            // terminationHandler зовётся с фонового потока Process, поэтому
            // возвращаемся на главный актор явно: обращение к @MainActor-полям
            // из захваченного self иначе становится ошибкой на Swift 6.
            old.terminationHandler = { [weak self] _ in
                Task { @MainActor [weak self] in
                    self?.process = nil
                    self?.start(preserveUI: preserveUI)
                }
            }
            send("stop")
            DispatchQueue.global().asyncAfter(deadline: .now() + 13) {
                if old.isRunning { kill(old.processIdentifier, SIGKILL) }
            }
            return
        }
        if !preserveUI {                 // авто-рестарт не должен стирать встречу с экрана
            lines = []
            theses = []
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
        }
        _hintBuf = ""; _lastHintUI = .distantPast
        // демон мог умереть посреди генерации — hint_done уже не придёт,
        // без сброса кнопки Подсказка/Claude/Протокол залипали заблокированными
        isHinting = false
        isExpanding = false
        isClouding = false
        userStopped = false
        status = L.t("Запускаю…", "Starting…", "启动中…")
        statusIsError = false

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
            let capture = SystemAudioCapture()
            systemAudioCapture = capture
            Task { @MainActor in
                if await capture.start() == false { self.systemAudioCapture = nil }
                self.launchDaemon(preserveUI: preserveUI)
            }
            return
        }
        launchDaemon(preserveUI: preserveUI)
    }

    /// Запуск python-демона. Отделён от start(), потому что захват звука
    /// поднимается асинхронно, а демон обязан стартовать ПОСЛЕ него.
    private func launchDaemon(preserveUI: Bool) {
        let p = Process()
        p.executableURL = suflerRoot.appendingPathComponent(".venv/bin/python")
        p.arguments = ["src/daemon.py"]
        p.currentDirectoryURL = suflerRoot

        let outPipe = Pipe()
        let inPipe = Pipe()
        p.standardOutput = outPipe
        p.standardInput = inPipe
        // stderr — в лог: без него сбои демона невидимы (logs/daemon.err.log)
        let logDir = suflerRoot.appendingPathComponent("logs")
        try? FileManager.default.createDirectory(at: logDir, withIntermediateDirectories: true)
        let errURL = logDir.appendingPathComponent("daemon.err.log")
        // append, не пересоздание: авто-рестарт после крэша затирал трейсбек
        // ровно в момент, когда он нужен для диагноза
        if !FileManager.default.fileExists(atPath: errURL.path) {
            FileManager.default.createFile(atPath: errURL.path, contents: nil)
        }
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
            isRunning = true
            beginSleepGuard()
            startClock()
            // сохранённые дефолты — новому демону (stdin буферизуется до готовности)
            for (key, on) in [("hints", hintsOn), ("theses", thesesOn), ("cloud", cloudOn)] where !on {
                send("set \(key) off")
            }
            lastEventAt = Date()
            watchdog?.invalidate()
            watchdog = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
                Task { @MainActor [weak self] in self?.checkAlive() }
            }
        } catch {
            fail(L.t("Не удалось начать запись: \(error.localizedDescription)", "Could not start recording: \(error.localizedDescription)", "无法开始录音：\(error.localizedDescription)"))
        }
    }

    func stop() {
        userStopped = true
        MeetingProcessingService.shared.expectResult()
        watchdog?.invalidate()
        watchdog = nil
        send("stop")
        // Демону нужно успеть: запустить graph_updater и закрыть аудио-стримы.
        // 1.5с не хватало на длинной встрече — обновление графа терялось.
        let p = process  // сильный захват: добить именно ЭТОТ демон, не преемника
        DispatchQueue.global().asyncAfter(deadline: .now() + 8.0) {
            if let p, p.isRunning { p.terminate() }
        }
        // Зависший в finally демон (мёртвый PortAudio-стрим не закрывается) жил
        // дальше и держал flock daemon.lock — следующий Старт молча отскакивал
        // («уже слушает в другом окне»). SIGKILL гарантирует смерть и снятие lock.
        DispatchQueue.global().asyncAfter(deadline: .now() + 12.0) {
            if let p, p.isRunning { kill(p.processIdentifier, SIGKILL) }
        }
        isRunning = false
        isExpanding = false
        endSleepGuard()
        stopClock()
        // Тап гасим не сразу: демон ещё дописывает хвост записи и закрывает
        // стримы. Снять устройство из-под него — это ровно тот обрыв, который
        // 05.08 стоил сорока минут встречи.
        let tap = systemAudioTap
        systemAudioTap = nil
        let capture = systemAudioCapture
        systemAudioCapture = nil
        DispatchQueue.main.asyncAfter(deadline: .now() + 13.0) {
            if #available(macOS 14.4, *) { (tap as? SystemAudioTap)?.stop() }
            if #available(macOS 13.0, *) {
                Task { @MainActor in await (capture as? SystemAudioCapture)?.stop() }
            }
        }
        status = L.t("Останавливаю…", "Stopping…", "停止中…")
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
        if #available(macOS 14.4, *) { (systemAudioTap as? SystemAudioTap)?.stop() }
        systemAudioTap = nil
        // Захват переживать демона не должен: без читателя поток пишется в
        // никуда, а манифест сбивал бы с толку следующую встречу.
        if #available(macOS 13.0, *) {
            let capture = systemAudioCapture
            systemAudioCapture = nil
            Task { @MainActor in await (capture as? SystemAudioCapture)?.stop() }
        }
        stdoutHandle?.readabilityHandler = nil
        stdoutHandle = nil
        try? errHandle?.close()
        errHandle = nil
        let wasRunning = isRunning
        isRunning = false
        stopClock()
        isHinting = false   // ждать hint_done/cloud_done от мёртвого демона бессмысленно
        isExpanding = false
        disarmHintTimeout()
        isClouding = false
        watchdog?.invalidate()
        watchdog = nil
        // Статусы читает человек на встрече, а не разработчик в логах. «Демон
        // умер», «нет heartbeat» ему ничего не говорят — важно другое: пишется
        // ли встреча прямо сейчас и надо ли что-то делать руками.
        guard wasRunning, !userStopped else {
            endSleepGuard()   // записи больше нет — маку можно спать
            status = L.t("Остановлен", "Stopped", "已停止")
        statusIsError = false
            return
        }
        guard restartAttempts < 3 else {
            // Три попытки подряд не помогли — молчать нельзя: человек уверен,
            // что встреча пишется, а запись давно встала. Страж сна тоже
            // снимаем: иначе провалившаяся запись навсегда запрещала маку
            // спать — до перезапуска приложения.
            endSleepGuard()
            fail("⛔️ Запись остановилась и не восстановилась. Нажмите «Слушать встречу» ещё раз")
            return
        }
        restartAttempts += 1
        fail(L.t("Запись прервалась — восстанавливаю (\(restartAttempts) из 3)", "Recording dropped — recovering (\(restartAttempts) of 3)", "录音中断——恢复中（第 \(restartAttempts)/3 次）"))
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
            guard let self, !self.userStopped, !self.isRunning else { return }
            self.start(preserveUI: true)
        }
    }

    /// Процесс жив, но heartbeat молчит 100с (hb идёт раз в 30с) — демон завис.
    /// Раньше это требовало ручного Стоп/Старт посреди встречи.
    private func checkAlive() {
        guard isRunning, let p = process, p.isRunning else { return }
        guard Date().timeIntervalSince(lastEventAt) > 100 else { return }
        fail(L.t("Запись замерла — перезапускаю", "Recording stalled — restarting", "录音停滞——正在重启"))
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
        armHintTimeout()
        send("hint")
    }

    func requestSummary() {
        guard isRunning, !isHinting else { return }
        hint = ""
        _hintBuf = ""; _lastHintUI = .distantPast
        isHinting = true
        armHintTimeout()
        send("summary")
    }

    /// ⏮: хвосты прошлых встреч по текущей теме нити — из графа в нить.
    /// Не занимает панель подсказки: ответ дописывается в нить строками ⏮,
    /// поэтому isHinting не трогаем — подсказку можно просить параллельно.
    /// Состояние ставим до отправки команды: два быстрых клика не успеют
    /// положить в очередь две одинаковые генерации до ответа демона.
    func requestExpand() {
        guard isRunning, !isExpanding else { return }
        isExpanding = true
        send("expand")
    }

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
        send("ask " + q)
    }

    private func send(_ cmd: String) {
        guard let fh = stdinPipe?.fileHandleForWriting,
              let data = (cmd + "\n").data(using: .utf8) else { return }
        try? fh.write(contentsOf: data)
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
            case "status":
                status = text
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
            case "expand_started":
                isExpanding = true
            case "expand_done":
                isExpanding = false
            case "thesis":
                theses.append(text)
                if theses.count > 200 { theses.removeFirst(theses.count - 200) }
            case "hint":
                // троттл ~30fps: hint растёт по токену, растущий Text = O(n²)
                _hintBuf += text
                if Date().timeIntervalSince(_lastHintUI) >= 0.033 {
                    hint = _hintBuf; _lastHintUI = Date()
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
