import ActivityKit
import AVFoundation
import Foundation
import UIKit

/// Запись с продолжением в фоне (Background Audio).
///
/// Правила платформы: стартуем ТОЛЬКО с экрана (из фона iOS не даст),
/// прерывание звонком ловим и возобновляем. Файл по стопу уезжает в
/// iCloud-папку импорта — дальше всё делает Mac.
///
/// Всё, что здесь написано про прерывания, контейнер и место записи, стоит
/// денег ровно один раз: час чужой встречи не переснять. Поэтому запись
/// живёт в Documents (не в tmp, который система чистит) и в CAF (не в M4A,
/// который без штатного stop() остаётся без атома `moov` и не читается ничем).
@MainActor
final class Recorder: NSObject, ObservableObject, AVAudioRecorderDelegate {
    enum Kind: String, CaseIterable, Identifiable {
        // rawValue — стабильный идентификатор (уходит в Live Activity и в
        // имя файла), подпись для человека берётся из `title`.
        case meeting, note, diary
        var id: String { rawValue }
        var title: String {
            switch self {
            case .meeting: return L.t("Встреча", "Meeting", "会议")
            case .note: return L.t("Заметка", "Note", "笔记")
            case .diary: return L.t("Дневник", "Diary", "日记")
            }
        }
        /// Префикс имени файла — по нему Mac выбирает конвейер.
        var prefix: String {
            switch self {
            case .meeting: return ""
            case .note: return "note_"
            case .diary: return "diary_"
            }
        }
    }

    @Published var isRecording = false
    @Published var elapsed: TimeInterval = 0
    @Published var level: Float = 0          // 0…1 для волны
    @Published var lastResult: String?       // статус доставки/очереди

    /// Запись идёт, но в файл ничего не прибавляется.
    ///
    /// Ровно то, что стоило получаса встречи 03.08: старая сборка считала время
    /// по стенным часам и не ловила прерывания, поэтому на экране бежали
    /// тридцать минут, а в файле осталась сорок одна секунда. Часы с тех пор
    /// честные (`r.currentTime`), но честные часы молчат: они просто перестают
    /// расти, и человек, не глядя в них в упор, этого не замечает. Здесь —
    /// явный флаг, чтобы экран мог сказать словами.
    @Published private(set) var stalled = false

    /// Взвод: микрофон занят (идёт звонок), запись начнётся сама, как
    /// только его отдадут. «Слушать сразу» на iOS упирается ровно сюда:
    /// сам звонок не запишет никто (сессия CallKit выше любой другой), но
    /// открытое во время звонка приложение не должно требовать второго
    /// нажатия после него (№167).
    @Published private(set) var armed = false
    /// Текст взвода — отдельный канал: доставка пишет в `lastResult`, и
    /// баннер взвода терял бы причину («Уехало на Mac» с иконкой телефона —
    /// GLM r1 по #497).
    @Published private(set) var armedStatus: String?
    private var armedKind: Kind = .meeting
    private var armedAt: Date?
    private var armTimer: Timer?
    /// Как часто пробуем микрофон во взведённом состоянии. Проба дешёвая
    /// (setActive + record), а `.ended` после чужого звонка нам никто не шлёт —
    /// сессии у нас в этот момент ещё нет.
    nonisolated static let armProbeEvery: TimeInterval = 5
    /// Сколько живёт взвод. В фоне процесс спит и таймер не тикает: старт
    /// из свёрнутого приложения платформа не даёт. Вернулись на экран через
    /// пять минут — стартуем; вернулись через три часа «посмотреть» — не
    /// должны получить запись, о которой не просили (DS r1 по #497).
    nonisolated static let armLifetimeMinutes = 30
    nonisolated static var armLifetime: TimeInterval { TimeInterval(armLifetimeMinutes * 60) }
    /// Последняя проба микрофона. Срок взвода — это НЕ возраст взвода, а
    /// пауза между пробами: пока приложение открыто, пробы идут каждые 5 с
    /// и срок не тикает (человек, державший экран весь звонок, не получает
    /// «истекло» — DS r2); в фоне таймер стоит, и первый тик после возврата
    /// видит паузу — вернулись через часы, значит запись не ждут (DS r1).
    private var lastProbeAt: Date?
    /// Почему ждём — для текста и иконки, обновляется каждой пробой (DS r2).
    @Published private(set) var armedBecause: StartFailure?
    /// Ключ типа записи в UserDefaults — один на экран и интент (GLM r1).
    nonisolated static let kindStorageKey = "record.kind"
    /// Запрос разрешения уже в полёте — второй старт (интент поверх
    /// автостарта) не должен открыть второй промпт и второй рекордер (DS r1).
    /// Штамп, а не флаг: колбэк промпта может не прийти (уход в фон с
    /// открытым промптом), и флаг навсегда глушил бы кнопку (GLM r2).
    private var permissionRequestedAt: Date?
    nonisolated static let permissionRequestStale: TimeInterval = 30

    /// Почему старт не удался.
    enum StartFailure: Equatable {
        /// Микрофон запрещён в Настройках — ждать нечего.
        case permissionDenied
        /// Места нет — ждать нечего.
        case lowStorage
        /// Сессию не отдали: звонок держит приоритет — отдадут после него.
        case sessionBusy
        /// Рекордер не стартовал: вход у другого приложения — может отпустить.
        case recorderBusy
        case other
    }

    /// Ждать ли микрофон после неудачного старта. Взводимся только на
    /// временные причины; запрет и пустой диск ожиданием не лечатся.
    nonisolated static func shouldArm(after failure: StartFailure) -> Bool {
        switch failure {
        case .sessionBusy, .recorderBusy: return true
        case .permissionDenied, .lowStorage, .other: return false
        }
    }

    /// Открыли приложение — писать сразу? Только холодный старт (не возврат
    /// из фона), только с включённой настройкой, только если ничего не
    /// идёт (иначе возврат на экран посреди записи запускал бы вторую) и
    /// только когда доставка настроена: первый запуск без папки iCloud
    /// выстреливал бы промпт микрофона и писал в никуда (критика GLM/DS r1).
    nonisolated static func shouldAutoStart(enabled: Bool, coldLaunch: Bool,
                                            isRecording: Bool, armed: Bool,
                                            deliveryReady: Bool) -> Bool {
        enabled && coldLaunch && !isRecording && !armed && deliveryReady
    }

    nonisolated static func armExpired(armedAt: Date, now: Date) -> Bool {
        now.timeIntervalSince(armedAt) > armLifetime
    }

    /// Пауза между пробами длиннее срока — приложение спало дольше, чем
    /// человек готов ждать записи, о которой не просил.
    nonisolated static func armExpired(lastProbeAt: Date, now: Date) -> Bool {
        armExpired(armedAt: lastProbeAt, now: now)
    }

    /// Честный текст взвода: причина — та, что была, а не всегда «звонок»
    /// (GLM/DS r1); старт обещан только открытому приложению.
    nonisolated static func armMessage(for failure: StartFailure) -> String {
        let m = armLifetimeMinutes
        switch failure {
        case .recorderBusy:
            return L.t("Микрофон занят другим приложением. Стартую сам, как только его отпустят, — пока приложение открыто (или при возврате в течение \(m) минут)",
                       "Another app holds the microphone. I start by myself once it lets go — while the app is open (or on return within \(m) minutes)",
                       "麦克风被其他应用占用。一旦释放就会自动开始——需保持应用打开（或在 \(m) 分钟内返回）")
        default:
            // isBusy приходит и от чужого приложения, не только от звонка —
            // называть причину точнее, чем знаем, нельзя (GLM r2)
            return L.t("Жду микрофон: идёт звонок или его держит другое приложение. Стартую сам, как только его отдадут, — пока приложение открыто (или при возврате в течение \(m) минут)",
                       "Waiting for the microphone: a call is on or another app holds it. I start by myself when it is released — while the app is open (or on return within \(m) minutes)",
                       "等待麦克风：正在通话或被其他应用占用。一旦释放就会自动开始——需保持应用打开（或在 \(m) 分钟内返回）")
        }
    }

    /// Ошибка аудиосессии → причина. `insufficientPriority` ('!pri') — ровно
    /// то, что iOS отвечает, пока микрофон у звонка; `isBusy` — то же от
    /// другого приложения.
    nonisolated static func failure(for error: Error) -> StartFailure {
        let code = (error as NSError).code
        if code == AVAudioSession.ErrorCode.insufficientPriority.rawValue
            || code == AVAudioSession.ErrorCode.isBusy.rawValue {
            return .sessionBusy
        }
        return .other
    }

    /// Последняя запись на телефоне — то, чем можно поделиться прямо сейчас.
    ///
    /// Отдельное published-поле, а не чтение папки прямо в теле экрана:
    /// SwiftUI не следит за файловой системой и не перерисовал бы кнопку ни
    /// после стопа, ни после доставки.
    @Published private(set) var lastRecording: URL? = Inbox.lastRecording

    func refreshLastRecording() {
        lastRecording = Inbox.lastRecording
    }

    /// Последняя длительность, на которой файл ещё рос.
    private var lastGrowth: (at: Date, seconds: TimeInterval)?

    /// Сколько раз подряд пытались поднять вставшую запись.
    private var resumeAttempts = 0
    /// После этого числа неудач файл закрывается и открывается новый.
    nonisolated static let maxResumeAttempts = 3

    /// Что делать после неудачной попытки поднять запись.
    enum StallAction: Equatable {
        /// Пробовать ещё: чужое приложение может отпустить вход в любой момент.
        case retry
        /// Хватит: закрыть файл и продолжить встречу следующим.
        case rotate
    }

    /// Решение вынесено отдельной функцией, чтобы политика проверялась тестом,
    /// а не живой встречей — как это было 03.08 и снова 06.08.
    nonisolated static func actionAfterFailedResume(attempts: Int) -> StallAction {
        attempts >= maxResumeAttempts ? .rotate : .retry
    }

    /// Идёт ли системное прерывание (звонок, ВКС). Пока идёт — микрофон
    /// принадлежит звонку, и «застой» записи это ПАУЗА, а не поломка.
    private var interrupted = false

    /// Пытаться ли поднимать вставшую запись прямо сейчас.
    ///
    /// 07.08, встреча 30+ минут: звонок забрал микрофон, сторож застоя
    /// этого не знал — три «неудачных» resume, ротация, и от встречи остался
    /// 40-килобайтный огрызок с мёртвой записью. Во время прерывания resume
    /// не имеет смысла (iOS не вернёт вход до конца звонка) и вреден:
    /// исчерпывает попытки и рубит файл. Ждём `.ended` — и продолжаем ТОТ ЖЕ
    /// файл. Политика вынесена статикой, чтобы её держал тест, а не встреча.
    nonisolated static func shouldAutoResume(stalled: Bool, interrupted: Bool) -> Bool {
        stalled && !interrupted
    }

    /// Когда началось прерывание. nil — прерывания нет.
    private var interruptedAt: Date?
    /// Когда последний раз проверяли, не вернулся ли вход.
    private var lastInterruptionProbe: Date?

    /// Через сколько после начала прерывания начинаем проверять вход.
    nonisolated static let probeAfterInterruption: TimeInterval = 60
    /// Как часто проверяем дальше.
    nonisolated static let probeEvery: TimeInterval = 30

    /// Пора ли проверить, освободился ли микрофон.
    ///
    /// `.ended` от iOS **не гарантирован** — это записано в документации
    /// Apple и подтверждается соседним комментарием в этом же файле («iOS
    /// далеко не всегда присылает `.ended`»). Но флаг `interrupted` снимался
    /// только по нему: не пришло — и сторож застоя молчит вечно, потому что
    /// считает застой законной паузой. Таймер при этом идёт, плашка на
    /// локскрине показывает запись, а файл не растёт. Человек узнаёт об этом
    /// после встречи (аудит 0.46.0, P0-9).
    ///
    /// Ждать первую минуту осмысленно: короткий звонок закончится сам, и
    /// `.ended` придёт штатно. Дальше пробуем редко — проба не бесплатна по
    /// смыслу, хотя и безвредна: во время живого звонка `record()` просто
    /// вернёт false.
    nonisolated static func shouldProbeInterruption(
        interruptedFor: TimeInterval,
        sinceLastProbe: TimeInterval?
    ) -> Bool {
        guard interruptedFor >= probeAfterInterruption else { return false }
        guard let sinceLastProbe else { return true }
        return sinceLastProbe >= probeEvery
    }
    /// Что пишем сейчас — нужно, чтобы продолжить тем же типом после ротации.
    private var currentKind: Kind = .meeting

    /// Сколько терпим неподвижное `currentTime`, прежде чем поднять тревогу.
    /// Три секунды: короче — ложные срабатывания на дрожании таймера, длиннее —
    /// человек успевает отвернуться.
    nonisolated static let stallAfter: TimeInterval = 3

    /// Пора ли объявлять запись вставшей: длительность файла не растёт дольше
    /// порога. Вынесено отдельно, чтобы правило проверялось тестом, а не только
    /// глазами на живой встрече.
    nonisolated static func isStalled(fileSeconds: TimeInterval,
                                      lastGrowthSeconds: TimeInterval,
                                      sinceLastGrowth: TimeInterval) -> Bool {
        fileSeconds <= lastGrowthSeconds && sinceLastGrowth > stallAfter
    }

    private var recorder: AVAudioRecorder?
    private var timer: Timer?
    private var activity: Activity<RecordActivityAttributes>?
    private var observers: [NSObjectProtocol] = []

    /// Запас, ниже которого начинать час записи бессмысленно (~96 кбит/с ≈ 43 МБ/час).
    private static let minFreeBytes: Int64 = 300_000_000

    override init() {
        super.init()
        observeSession()
    }

    func start(kind: Kind) {
        // Три триггера старта (кнопка/автостарт, интент, проба взвода) не
        // взаимоисключены во времени: второй рекордер молча перетирал бы
        // первый, оставив незакрытый файл (DS r1 по #497).
        guard !isRecording, recorder == nil else { return }
        // Разрешение спрашиваем ДО старта. Раньше не спрашивали вовсе:
        // setActive при запрещённом микрофоне обычно не бросает, система
        // просто подаёт тишину — и человек писал час, получая пустой файл
        // и бодрое «Уехало на Mac».
        switch AVAudioApplication.shared.recordPermission {
        case .granted:
            break
        case .undetermined:
            if let asked = permissionRequestedAt,
               Date().timeIntervalSince(asked) < Self.permissionRequestStale {
                return          // промпт ещё на экране — второй не открываем
            }
            permissionRequestedAt = Date()
            AVAudioApplication.requestRecordPermission { [weak self] granted in
                Task { @MainActor in
                    guard let self else { return }
                    self.permissionRequestedAt = nil
                    if granted {
                        self.start(kind: kind)
                    } else {
                        self.startFailed(.permissionDenied, kind: kind,
                                         message: L.t("Без доступа к микрофону запись невозможна",
                                                      "Recording needs microphone access",
                                                      "录音需要麦克风权限"))
                    }
                }
            }
            return
        default:
            // Через startFailed, а не напрямую: политика «на запрет и место не
            // взводимся» живёт в shouldArm, и выходить в обход неё нельзя
            // (GLM M3 / DS M2 по #497)
            startFailed(.permissionDenied, kind: kind,
                        message: L.t("Микрофон запрещён: Настройки › Charoite › Микрофон",
                                     "Microphone denied: Settings › Charoite › Microphone",
                                     "麦克风被拒绝：设置 › Charoite › 麦克风"))
            return
        }

        guard Self.freeBytes() > Self.minFreeBytes else {
            startFailed(.lowStorage, kind: kind,
                        message: L.t("Мало места на iPhone — освободите 300 МБ",
                                     "Low storage — free up 300 MB",
                                     "存储空间不足 — 请释放 300 MB"))
            return
        }

        let session = AVAudioSession.sharedInstance()
        do {
            // .measurement: режим отключает обработку голоса, из-за которой
            // AAC-запись встречи звучит «телефонно» и хуже распознаётся.
            //
            // allowBluetooth помечен deprecated в свежих SDK (переименован в
            // allowBluetoothHFP), но нового имени нет в iOS 18 SDK, на котором
            // собирает CI, — а собираться должно и там, и на машине с новым
            // Xcode. Оставляем совместимое имя до обновления раннеров.
            try session.setCategory(.playAndRecord, mode: .measurement,
                                    options: [.allowBluetooth])
            // Системные алерты (будильник, таймер, баннер с звуком) прерывают
            // сессию так же, как звонок: запись встаёт, а человек в это время
            // говорит. Просим систему не рвать нас по мелочи — iOS 14.5+.
            try? session.setPrefersNoInterruptionsFromSystemAlerts(true)
            try session.setActive(true)
        } catch {
            startFailed(Self.failure(for: error), kind: kind,
                        message: L.t("Аудиосессия не открылась: \(error.localizedDescription)",
                                     "Audio session failed: \(error.localizedDescription)",
                                     "音频会话失败：\(error.localizedDescription)"))
            return
        }

        // Секунды в штампе: без них две заметки внутри одной минуты давали
        // одно имя, и вторая физически затирала первую в папке на Mac.
        // Секунда — не гарантия: старт-стоп-старт в одну секунду коллидирует,
        // а AVAudioRecorder молча перезаписывает существующий файл (ревью
        // 15.08) — имя уникализируется суффиксом до свободного.
        let base = "\(kind.prefix)iphone_\(Self.stamp(Date()))"
        var url = Inbox.inProgress.appendingPathComponent(base + ".caf")
        var attempt = 2
        while FileManager.default.fileExists(atPath: url.path), attempt < 100 {
            url = Inbox.inProgress.appendingPathComponent("\(base)_\(attempt).caf")
            attempt += 1
        }
        if FileManager.default.fileExists(atPath: url.path) {
            // и сотый занят: UUID-хвост вместо тихой перезаписи последнего
            url = Inbox.inProgress.appendingPathComponent(
                "\(base)_\(UUID().uuidString.prefix(8)).caf")
        }
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 44_100,
            AVNumberOfChannelsKey: 1,
            AVEncoderBitRateKey: 96_000,
        ]
        do {
            let r = try AVAudioRecorder(url: url, settings: settings)
            r.delegate = self
            r.isMeteringEnabled = true
            guard r.record() else {
                // init рекордера уже создал файл: пустышка при каждой пробе
                // взвода ехала бы на Mac «записью» (GLM I2 / DS M3 по #497)
                try? FileManager.default.removeItem(at: url)
                // Сессию отдаём: иначе индикатор микрофона горит весь взвод,
                // а проба активирует её заново сама (GLM r2)
                try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
                startFailed(.recorderBusy, kind: kind,
                            message: L.t("Запись не стартовала — микрофон занят другим приложением?",
                                         "Recording did not start — microphone busy?",
                                         "录音未开始 — 麦克风被占用？"))
                return
            }
            disarm(quiet: true)
            recorder = r
            isRecording = true
            currentKind = kind
            resumeAttempts = 0
            elapsed = 0
            stalled = false
            lastGrowth = nil
            lastResult = nil
            timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in
                Task { @MainActor [weak self] in self?.tick() }
            }
            startActivity(kind: kind)
        } catch {
            try? FileManager.default.removeItem(at: url)
            disarm(quiet: true)
            lastResult = L.t("Запись не стартовала: \(error.localizedDescription)",
                             "Recording failed: \(error.localizedDescription)",
                             "录音失败：\(error.localizedDescription)")
        }
    }

    /// Неудачный старт: либо честное сообщение, либо взвод и ожидание.
    private func startFailed(_ failure: StartFailure, kind: Kind, message: String) {
        guard Self.shouldArm(after: failure) else {
            disarm(quiet: true)
            lastResult = message
            return
        }
        arm(kind: kind, because: failure)
    }

    /// Ждать микрофон и стартовать самим, как только его отдадут.
    func arm(kind: Kind, because failure: StartFailure = .sessionBusy) {
        armedKind = kind
        // Причина могла смениться (звонок кончился, вход забрало другое
        // приложение) — текст и иконку обновляем на каждой пробе; та же
        // строка не мигает (GLM/DS r2)
        armedBecause = failure
        armedStatus = Self.armMessage(for: failure)
        guard !armed else { return }        // уже ждём — таймер не трогаем
        armed = true
        armedAt = Date()
        lastProbeAt = Date()
        armTimer?.invalidate()
        armTimer = Timer.scheduledTimer(withTimeInterval: Self.armProbeEvery, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in self?.probeArmed() }
        }
    }

    /// Человек передумал: та же большая кнопка во взведённом состоянии.
    func disarm(quiet: Bool = false) {
        armTimer?.invalidate()
        armTimer = nil
        guard armed else { return }
        armed = false
        armedAt = nil
        lastProbeAt = nil
        armedBecause = nil
        armedStatus = nil
        if !quiet {
            lastResult = L.t("Ожидание микрофона отменено", "Waiting for the microphone cancelled", "已取消等待麦克风")
        }
    }

    private func probeArmed() {
        guard armed, !isRecording else {
            disarm(quiet: true)
            return
        }
        let now = Date()
        let previous = lastProbeAt ?? now
        lastProbeAt = now
        if Self.armExpired(lastProbeAt: previous, now: now) {
            disarm(quiet: true)
            let m = Self.armLifetimeMinutes
            lastResult = L.t("Ожидание микрофона истекло (\(m) мин) — нажмите запись",
                             "Waiting for the microphone expired (\(m) min) — tap record",
                             "等待麦克风已超时（\(m) 分钟）——请点击录音")
            return
        }
        start(kind: armedKind)      // успех снимает взвод сам; неудача — ждём дальше
    }

    /// Прерывания и смена маршрута. Без этого звонок посреди встречи оставлял
    /// запись на паузе навсегда, а таймер на экране и в Dynamic Island
    /// продолжал считать — человек узнавал о потере через час.
    private func observeSession() {
        let nc = NotificationCenter.default
        observers.append(nc.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: AVAudioSession.sharedInstance(), queue: .main
        ) { [weak self] note in
            guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                  let type = AVAudioSession.InterruptionType(rawValue: raw) else { return }
            Task { @MainActor [weak self] in self?.handleInterruption(type) }
        })
        observers.append(nc.addObserver(
            forName: AVAudioSession.mediaServicesWereResetNotification,
            object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, self.isRecording else { return }
                self.lastResult = L.t("Аудиослужба перезапущена — запись остановлена, файл сохранён",
                                      "Audio service reset — recording stopped, file kept",
                                      "音频服务已重置 — 录音停止，文件已保留")
                self.stop()
            }
        })
        observers.append(nc.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: nil, queue: .main
        ) { [weak self] note in
            guard let raw = note.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
                  raw == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue else { return }
            Task { @MainActor [weak self] in
                guard let self, self.isRecording else { return }
                self.lastResult = L.t("Гарнитура отключилась — пишем встроенным микрофоном",
                                      "Headset disconnected — using built-in mic",
                                      "耳机已断开 — 使用内置麦克风")
            }
        })
    }

    private func handleInterruption(_ type: AVAudioSession.InterruptionType) {
        guard isRecording, recorder != nil else { return }
        switch type {
        case .began:
            interrupted = true
            interruptedAt = Date()
            lastInterruptionProbe = nil
            lastResult = L.t("Пауза: идёт звонок — микрофон у него. Запись продолжится после",
                             "Paused: a call owns the microphone. Recording resumes after it",
                             "已暂停：通话占用麦克风。通话结束后继续录音")
        case .ended:
            interrupted = false
            interruptedAt = nil
            lastInterruptionProbe = nil
            resumeAfterCall(attempt: 1)
        @unknown default:
            break
        }
    }

    /// Продолжить ТОТ ЖЕ файл после конца звонка. Сессия освобождается
    /// лениво — первая попытка сразу после `.ended` нередко упирается в ещё
    /// занятый вход, поэтому до трёх заходов с паузой, и только потом
    /// честное «сохраняю записанное».
    private func resumeAfterCall(attempt: Int) {
        guard isRecording, !interrupted, let r = recorder else { return }
        try? AVAudioSession.sharedInstance().setActive(true)
        if r.record() {
            lastResult = nil
            return
        }
        guard attempt >= 3 else {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { [weak self] in
                self?.resumeAfterCall(attempt: attempt + 1)
            }
            return
        }
        lastResult = L.t("Запись оборвалась после звонка — сохраняю записанное",
                         "Recording broke after the call — saving what we have",
                         "通话后录音中断 — 正在保存已录内容")
        stop()
    }

    /// Таймер в Dynamic Island / на локскрине: запись видна, даже когда
    /// телефон лежит экраном к столу и приложение свернули.
    private func startActivity(kind: Kind) {
        guard ActivityAuthorizationInfo().areActivitiesEnabled else { return }
        // staleDate: плашка обязана потускнеть, если приложение умерло и
        // некому вызвать end() — иначе она врёт про идущую запись часами.
        activity = try? Activity.request(
            attributes: RecordActivityAttributes(kind: kind.rawValue),
            content: .init(state: .init(startedAt: Date()),
                           staleDate: Date().addingTimeInterval(900)))
    }

    func stop() {
        guard let r = recorder else { return }
        r.stop()                       // финализация контейнера
        timer?.invalidate()
        timer = nil
        isRecording = false
        stalled = false
        interrupted = false   // флаг не должен пережить запись
        interruptedAt = nil
        lastInterruptionProbe = nil
        lastGrowth = nil
        level = 0
        let url = r.url
        recorder = nil
        if let a = activity {
            activity = nil
            Task { await a.end(nil, dismissalPolicy: .immediate) }
        }
        // Освобождаем сессию: иначе оранжевый индикатор микрофона горит после
        // стопа, а чужая музыка не возобновляется — для приложения про
        // приватность это выглядит хуже любого бага.
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        Task { [weak self] in
            await Inbox.deliver(url) { msg in self?.lastResult = msg }
            self?.refreshLastRecording()
        }
    }

    private func tick() {
        guard let r = recorder else { return }
        // Время берём у рекордера, а не у стенных часов: при прерывании
        // запись стоит, и только currentTime покажет реальную длину файла.
        elapsed = r.currentTime
        r.updateMeters()
        // −60…0 дБ → 0…1
        level = max(0, min(1, (r.averagePower(forChannel: 0) + 60) / 60))
        checkGrowth(r.currentTime)
        // Пока не поднялись — пробуем на каждом такте: чужое приложение может
        // отпустить вход через секунду, а может через минуту. Но не во время
        // звонка: там застой — это пауза, и попытки лишь исчерпают лимит.
        if Self.shouldAutoResume(stalled: stalled, interrupted: interrupted) { tryResume() }
        probeInterruptionIfNeeded()
    }

    /// Проверить, не кончилось ли прерывание, о конце которого нам не сказали.
    ///
    /// Отдельно от `tryResume()` намеренно: тот считает попытки и на третьей
    /// ротирует файл. Во время живого звонка это резало бы встречу на куски
    /// — ровно та беда, ради которой прерывание и стало паузой. Проба ничего
    /// не тратит: не вышло — просто ждём дальше.
    private func probeInterruptionIfNeeded() {
        guard isRecording, interrupted, let since = interruptedAt, let r = recorder else { return }
        guard Self.shouldProbeInterruption(
            interruptedFor: Date().timeIntervalSince(since),
            sinceLastProbe: lastInterruptionProbe.map { Date().timeIntervalSince($0) }
        ) else { return }

        lastInterruptionProbe = Date()
        try? AVAudioSession.sharedInstance().setActive(true)
        guard r.record() else { return }      // звонок ещё идёт — ждём дальше

        interrupted = false
        interruptedAt = nil
        lastInterruptionProbe = nil
        stalled = false
        lastResult = L.t("Запись продолжается — звонок закончился",
                         "Recording resumed — the call has ended",
                         "录音已继续 — 通话已结束")
    }

    /// Растёт ли файл. Сравниваем длительность с прошлым тиком: замерла — значит
    /// запись стоит, что бы ни показывал индикатор и что бы ни думало приложение.
    ///
    /// Это ловит и то, о чём система нам не сообщила: прерывание без
    /// уведомления, отнятый другим приложением вход, тихо умершую сессию.
    private func checkGrowth(_ now: TimeInterval) {
        guard let last = lastGrowth else {
            lastGrowth = (Date(), now)
            return
        }
        if now > last.seconds {
            lastGrowth = (Date(), now)
            if stalled {
                stalled = false
                lastResult = L.t("Запись продолжается", "Recording resumed", "录音已恢复")
            }
            return
        }
        guard !stalled,
              Self.isStalled(fileSeconds: now,
                             lastGrowthSeconds: last.seconds,
                             sinceLastGrowth: Date().timeIntervalSince(last.at)) else { return }
        stalled = true
        if interrupted {
            // Звонок: микрофон у него до конца, resume бессмыслен и вреден —
            // просто ждём `.ended` и продолжаем тот же файл.
            return
        }
        // Раньше здесь была только надпись на экране — а телефон во время
        // встречи лежит экраном вниз, и человек узнавал о потере постфактум:
        // в файле полторы минуты вместо получаса. Теперь пытаемся поднять
        // запись сами. iOS далеко не всегда присылает `.ended` после
        // прерывания: если чужое приложение удержало вход, уведомления о
        // конце можно ждать вечно.
        resumeAttempts = 0
        tryResume()
    }

    /// Поднять вставшую запись. До `maxResumeAttempts` попыток, между ними —
    /// такт таймера; если не вышло, честно закрываем файл и начинаем новый,
    /// чтобы остаток встречи писался, а записанное уже точно уехало.
    private func tryResume() {
        guard isRecording, let r = recorder else { return }
        resumeAttempts += 1
        try? AVAudioSession.sharedInstance().setActive(true)
        if r.record() {
            stalled = false
            lastResult = L.t("Запись восстановлена автоматически",
                             "Recording resumed automatically",
                             "录音已自动恢复")
            return
        }
        guard Self.actionAfterFailedResume(attempts: resumeAttempts) == .rotate else { return }
        lastResult = L.t("Запись не поднялась — закрываю файл и начинаю новый",
                         "Could not resume — closing the file and starting a new one",
                         "无法恢复 — 正在关闭文件并开始新的录音")
        rotateFile()
    }

    /// Закрыть текущий файл и продолжить встречу в следующем.
    ///
    /// Потерять полчаса разговора хуже, чем получить встречу двумя кусками:
    /// конвейер на Mac принимает оба файла, а склейка — вопрос порядка по
    /// имени, в котором стоят секунды.
    private func rotateFile() {
        let kind = currentKind
        stop()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) { [weak self] in
            guard let self, !self.isRecording else { return }
            self.start(kind: kind)
        }
    }

    nonisolated func audioRecorderEncodeErrorDidOccur(_ recorder: AVAudioRecorder, error: Error?) {
        Task { @MainActor [weak self] in
            self?.lastResult = L.t("Сбой записи: \(error?.localizedDescription ?? "кодек")",
                                   "Recording error: \(error?.localizedDescription ?? "codec")",
                                   "录音错误：\(error?.localizedDescription ?? "编解码器")")
            self?.stop()
        }
    }

    nonisolated func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder,
                                                     successfully flag: Bool) {
        guard !flag else { return }
        Task { @MainActor [weak self] in
            self?.lastResult = L.t("Запись завершилась с ошибкой — файл может быть неполным",
                                   "Recording finished with an error — file may be incomplete",
                                   "录音异常结束 — 文件可能不完整")
        }
    }

    static func stamp(_ d: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd_HHmmss"
        f.locale = Locale(identifier: "en_US_POSIX")
        return f.string(from: d)
    }

    private static func freeBytes() -> Int64 {
        let home = URL(fileURLWithPath: NSHomeDirectory())
        let v = try? home.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        return v?.volumeAvailableCapacityForImportantUsage ?? .max
    }
}
