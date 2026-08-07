import ActivityKit
import AVFoundation
import Foundation

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
        // Разрешение спрашиваем ДО старта. Раньше не спрашивали вовсе:
        // setActive при запрещённом микрофоне обычно не бросает, система
        // просто подаёт тишину — и человек писал час, получая пустой файл
        // и бодрое «Уехало на Mac».
        switch AVAudioApplication.shared.recordPermission {
        case .granted:
            break
        case .undetermined:
            AVAudioApplication.requestRecordPermission { [weak self] granted in
                Task { @MainActor in
                    guard let self else { return }
                    if granted { self.start(kind: kind) }
                    else { self.lastResult = L.t("Без доступа к микрофону запись невозможна",
                                                 "Recording needs microphone access",
                                                 "录音需要麦克风权限") }
                }
            }
            return
        default:
            lastResult = L.t("Микрофон запрещён: Настройки › Charoite › Микрофон",
                             "Microphone denied: Settings › Charoite › Microphone",
                             "麦克风被拒绝：设置 › Charoite › 麦克风")
            return
        }

        guard Self.freeBytes() > Self.minFreeBytes else {
            lastResult = L.t("Мало места на iPhone — освободите 300 МБ",
                             "Low storage — free up 300 MB",
                             "存储空间不足 — 请释放 300 MB")
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
            lastResult = L.t("Аудиосессия не открылась: \(error.localizedDescription)",
                             "Audio session failed: \(error.localizedDescription)",
                             "音频会话失败：\(error.localizedDescription)")
            return
        }

        // Секунды в штампе: без них две заметки внутри одной минуты давали
        // одно имя, и вторая физически затирала первую в папке на Mac.
        let name = "\(kind.prefix)iphone_\(Self.stamp(Date())).caf"
        let url = Inbox.inProgress.appendingPathComponent(name)
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
                lastResult = L.t("Запись не стартовала — микрофон занят другим приложением?",
                                 "Recording did not start — microphone busy?",
                                 "录音未开始 — 麦克风被占用？")
                return
            }
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
            lastResult = L.t("Запись не стартовала: \(error.localizedDescription)",
                             "Recording failed: \(error.localizedDescription)",
                             "录音失败：\(error.localizedDescription)")
        }
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
        guard isRecording, let r = recorder else { return }
        switch type {
        case .began:
            lastResult = L.t("Пауза: звонок. Запись продолжится сама",
                             "Paused: call. Recording will resume",
                             "已暂停：来电。录音将自动继续")
        case .ended:
            try? AVAudioSession.sharedInstance().setActive(true)
            if r.record() {
                lastResult = nil
            } else {
                // Возобновить не удалось — честно говорим и закрываем файл,
                // чтобы записанное до звонка точно уехало на Mac.
                lastResult = L.t("Запись оборвалась после звонка — сохраняю записанное",
                                 "Recording broke after the call — saving what we have",
                                 "通话后录音中断 — 正在保存已录内容")
                stop()
            }
        @unknown default:
            break
        }
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
        // отпустить вход через секунду, а может через минуту.
        if stalled { tryResume() }
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
