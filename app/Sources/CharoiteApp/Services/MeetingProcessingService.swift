import Foundation

#if os(macOS)
import AppKit

struct MeetingProcessingSnapshot: Decodable, Equatable, Sendable {
    enum State: String, Decodable, Sendable {
        case processing
        case ready
        case error
        /// Запись есть, речи в ней нет — не ошибка, а результат.
        case empty
        /// Состояние из более новой версии конвейера.
        ///
        /// Без этого случая любое добавленное на стороне Python состояние
        /// роняло разбор всего снимка, и встреча просто исчезала из окна:
        /// строгий enum превращал совместимую правку в потерю данных.
        case unknown

        init(from decoder: Decoder) throws {
            let raw = try decoder.singleValueContainer().decode(String.self)
            self = State(rawValue: raw) ?? .unknown
        }
    }

    let schemaVersion: Int
    let meetingID: String
    let state: State
    let stage: String
    let startedAt: TimeInterval
    let updatedAt: TimeInterval
    let transcriptPath: String
    let notePath: String?
    let error: String?
    /// Какая часть длинной стенограммы разбирается и сколько их всего.
    let part: Int?
    let parts: Int?

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case meetingID = "meeting_id"
        case state
        case stage
        case startedAt = "started_at"
        case updatedAt = "updated_at"
        case transcriptPath = "transcript_path"
        case notePath = "note_path"
        case error
        case part
        case parts
    }

    /// Свой init с умолчаниями вместо memberwise: прогресс есть только у
    /// длинной встречи, и добавление такого поля не должно заставлять
    /// переписывать каждый тест, который снимок собирает.
    init(schemaVersion: Int, meetingID: String, state: State, stage: String,
         startedAt: TimeInterval, updatedAt: TimeInterval, transcriptPath: String,
         notePath: String?, error: String?,
         part: Int? = nil, parts: Int? = nil) {
        self.schemaVersion = schemaVersion
        self.meetingID = meetingID
        self.state = state
        self.stage = stage
        self.startedAt = startedAt
        self.updatedAt = updatedAt
        self.transcriptPath = transcriptPath
        self.notePath = notePath
        self.error = error
        self.part = part
        self.parts = parts
    }
}

enum MeetingProcessingPolicy {
    static let staleAfter: TimeInterval = 30 * 60
    static let visibleFor: TimeInterval = 24 * 60 * 60

    /// Сколько ждать первый статус после «Стоп». Конвейер объявляет о себе
    /// в первые секунды; три минуты тишины значат, что статус уже не появится.
    static let announceWithin: TimeInterval = 3 * 60

    /// «Запускаю обработку…» — обещание, а не заставка. Если конвейер не
    /// записал ни одного статуса (диск, права, ранний выход до первой
    /// записи), обещание обязано смениться честной ошибкой, а не висеть
    /// вечным спиннером.
    static func waitingExpired(since: Date, now: Date = Date()) -> Bool {
        now.timeIntervalSince(since) > announceWithin
    }

    static func resolvedState(
        _ snapshot: MeetingProcessingSnapshot,
        now: Date = Date()
    ) -> MeetingProcessingSnapshot.State {
        if snapshot.state == .processing,
           now.timeIntervalSince1970 - snapshot.updatedAt > staleAfter {
            return .error
        }
        return snapshot.state
    }

    static func latest(
        _ snapshots: [MeetingProcessingSnapshot],
        now: Date = Date()
    ) -> MeetingProcessingSnapshot? {
        snapshots
            .filter { now.timeIntervalSince1970 - $0.updatedAt <= visibleFor }
            .max {
                if $0.startedAt == $1.startedAt { return $0.updatedAt < $1.updatedAt }
                return $0.startedAt < $1.startedAt
            }
    }

    static func actionPath(
        for snapshot: MeetingProcessingSnapshot,
        now: Date = Date()
    ) -> String? {
        switch resolvedState(snapshot, now: now) {
        case .ready:
            return snapshot.notePath
        case .error, .empty:
            // И у ошибки, и у записи без речи полезен один и тот же ответ:
            // показать саму стенограмму — по ней видно, что там на самом деле.
            return snapshot.transcriptPath
        case .processing, .unknown:
            return nil
        }
    }

    /// Чем занят конвейер прямо сейчас — словами, а не кодом стадии.
    ///
    /// Отдельная чистая функция, а не ветка внутри вычисляемого свойства
    /// сервиса: главная строка, которую человек читает, пока ждёт, должна
    /// проверяться тестом.
    static func stageText(for snapshot: MeetingProcessingSnapshot) -> String {
        switch snapshot.stage {
        case "waiting_for_audio", "recovering":
            return L.t("Сохраняю запись…", "Saving the recording…", "正在保存录音…")
        case "rebuilding_transcript":
            return L.t("Пересобираю стенограмму…",
                       "Rebuilding the transcript…",
                       "正在重建逐字稿…")
        case "updating_graph":
            // На длинной встрече эта стадия висит минутами и внешне ничем не
            // отличается от зависшего процесса. Номер части — то, что отличает
            // работу от смерти, не заглядывая в логи. Одну часть не объявляем:
            // «часть 1 из 1» читается как начало долгого пути.
            if let part = snapshot.part, let parts = snapshot.parts, parts > 1 {
                return L.t("Обновляю граф встречи… часть \(part) из \(parts)",
                           "Updating the meeting graph… part \(part) of \(parts)",
                           "正在更新会议图谱…第 \(part)/\(parts) 部分")
            }
            return L.t("Обновляю граф встречи…",
                       "Updating the meeting graph…",
                       "正在更新会议图谱…")
        default:
            return L.t("Обрабатываю встречу…",
                       "Processing the meeting…",
                       "正在处理会议…")
        }
    }

    /// Сколько встреч показываем в истории. Store хранит статусы 14 дней;
    /// список нужен, чтобы «что там было вчера» находилось за один взгляд,
    /// а не чтобы заменить архив графа.
    static let historyLimit = 20

    /// Недавние встречи, новая первая.
    ///
    /// Один и тот же meeting_id может прийти из нескольких файлов (повтор
    /// пишет в тот же статус, но на диске могли остаться следы прошлых
    /// прогонов) — оставляем самую свежую запись о каждой встрече, иначе
    /// человек видит одну встречу дважды в разных состояниях.
    static func history(
        _ snapshots: [MeetingProcessingSnapshot],
        now: Date = Date(),
        limit: Int = historyLimit
    ) -> [MeetingProcessingSnapshot] {
        var freshest: [String: MeetingProcessingSnapshot] = [:]
        for snapshot in snapshots
        where now.timeIntervalSince1970 - snapshot.updatedAt <= Double(STATUS_KEEP_DAYS) * 86_400 {
            if let seen = freshest[snapshot.meetingID], seen.updatedAt >= snapshot.updatedAt {
                continue
            }
            freshest[snapshot.meetingID] = snapshot
        }
        return freshest.values
            .sorted {
                if $0.startedAt == $1.startedAt { return $0.updatedAt > $1.updatedAt }
                return $0.startedAt > $1.startedAt
            }
            .prefix(limit)
            .map { $0 }
    }

    /// Столько же дней, сколько хранит python-store (STATUS_KEEP_DAYS).
    static let STATUS_KEEP_DAYS = 14

    /// Можно ли предложить «Повторить обработку». Ошибкой считается и явный
    /// failed, и зависший processing (30 минут без записи статуса): для
    /// пользователя оба выглядят одинаково — результата нет. Повтор возможен,
    /// пока жива стенограмма: конвейер стартует от неё.
    static func canRetry(
        _ snapshot: MeetingProcessingSnapshot,
        transcriptExists: Bool,
        now: Date = Date()
    ) -> Bool {
        resolvedState(snapshot, now: now) == .error && transcriptExists
    }

    /// Что показывать в строке списка про повтор этой встречи.
    ///
    /// Раньше строка знала только «идёт какой-то повтор» — и на время повтора
    /// погашенная кнопка «Повторить» вырастала у всех встреч, включая готовые.
    /// Правильных состояния четыре, и они разные:
    /// - `hidden` — встрече повтор не нужен (готова, идёт, без стенограммы);
    /// - `ready` — упала, можно повторить;
    /// - `waiting` — упала, но сейчас повторяется другая: кнопка есть, ждёт;
    /// - `running` — именно её и повторяем: не кнопка, а «работаю».
    enum RetryControl: Equatable {
        case hidden, ready, waiting, running
    }

    static func retryControl(
        for snapshot: MeetingProcessingSnapshot,
        transcriptExists: Bool,
        retryingID: String?,
        now: Date = Date()
    ) -> RetryControl {
        // Сначала «running»: у повторяемой встречи статус уже переписан в
        // processing, и проверка состояния ниже спрятала бы индикатор ровно
        // на время работы.
        if retryingID == snapshot.meetingID { return .running }
        guard canRetry(snapshot, transcriptExists: transcriptExists, now: now) else {
            return .hidden
        }
        return retryingID == nil ? .ready : .waiting
    }

    /// Дождались ли мы «своего» статуса после нажатия кнопки.
    ///
    /// После «Стоп» свой статус — любой новый запуск конвейера (started_at не
    /// раньше нажатия). После «Повторить» так нельзя: store сохраняет
    /// started_at первого запуска, и повторный прогон приходит со СТАРЫМ
    /// started_at — по прежнему критерию он был бы невидим, и через три
    /// минуты честного ожидания UI объявил бы «конвейер молчит» про конвейер,
    /// который работает. Поэтому для повтора критерий другой: та же встреча
    /// и запись статуса СВЕЖЕЕ той, что человек видел, когда нажимал.
    ///
    /// Сравнение с видимым статусом, а не со временем нажатия: часы файла и
    /// часы приложения — разные источники, и пятисекундный допуск, который
    /// был здесь раньше, принимал ЗА РЕЗУЛЬТАТ ПОВТОРА тот самый статус
    /// ошибки, по которому кнопку и нажали (если жали сразу, как только
    /// ошибка появилась). UI мгновенно возвращался в «ошибка», кнопка
    /// «Повторить» появлялась снова — и следующее нажатие запускало вторую
    /// обработку поверх работающей первой.
    static func matchesExpectation(
        _ snapshot: MeetingProcessingSnapshot,
        since: Date,
        retry: RetryExpectation?
    ) -> Bool {
        if let retry {
            return snapshot.meetingID == retry.meetingID
                && snapshot.updatedAt > retry.afterUpdatedAt
        }
        return snapshot.startedAt >= since.timeIntervalSince1970 - 5
    }
}

extension MeetingProcessingSnapshot {
    /// Человеческое имя встречи для списка.
    ///
    /// graph_updater переименовывает файлы по теме разговора
    /// («2026-07-15_1415_Платёжный_провайдер.md»), поэтому тема уже лежит в
    /// имени стенограммы — выдумывать и лезть в граф не нужно. Пока разбор не
    /// дошёл до переименования, честнее показать дату, чем пустую строку.
    var title: String {
        let stem = URL(fileURLWithPath: transcriptPath)
            .deletingPathExtension().lastPathComponent
        let stamp = String(stem.prefix(15))
        var rest = String(stem.dropFirst(15))
        // Штамп бывает и с секундами: живая запись даёт «2026-08-03_113012»,
        // а graph_updater переименовывает её в «2026-08-03_1130_Тема». Резать
        // ровно 15 символов нельзя — от «…113012» оставалось «12», и в списке
        // сегодняшняя встреча называлась числом.
        if rest.count >= 2, rest.prefix(2).allSatisfy(\.isNumber) {
            rest.removeFirst(2)
        }
        while rest.first == "_" || rest.first == " " { rest.removeFirst() }
        for suffix in ["_minutes", "_hints", "_live", "_debrief"] where rest.hasSuffix(suffix) {
            rest.removeLast(suffix.count)
        }
        let name = rest.replacingOccurrences(of: "_", with: " ")
            .trimmingCharacters(in: .whitespaces)
        return name.isEmpty ? Self.dayAndTime(stamp) : name
    }

    /// Когда встречу начали. Штамп в имени — местное время машины, а
    /// started_at — момент, когда конвейер впервые записал статус; для списка
    /// нужен первый, он совпадает с тем, что человек помнит.
    var startedDate: Date {
        Date(timeIntervalSince1970: startedAt)
    }

    /// «31 июля, 14:15» из штампа «2026-07-31_1415».
    private static func dayAndTime(_ stamp: String) -> String {
        let parser = DateFormatter()
        parser.dateFormat = "yyyy-MM-dd_HHmm"
        guard let date = parser.date(from: stamp) else { return stamp }
        let out = DateFormatter()
        out.locale = Locale.current
        out.setLocalizedDateFormatFromTemplate("d MMMM HH:mm")
        return out.string(from: date)
    }
}

/// Чего мы ждём после нажатия «Повторить обработку».
///
/// `afterUpdatedAt` — отметка статуса, который человек видел на экране в этот
/// момент. Всё, что не свежее её, — прошлое, а не результат повтора.
/// `transcriptPath` хранится, чтобы повтор оставался возможен, даже если
/// конвейер промолчал и снимок статуса пропал из виду.
struct RetryExpectation: Equatable, Sendable {
    let meetingID: String
    let afterUpdatedAt: TimeInterval
    let transcriptPath: String
}

/// Команда повторной обработки: тот же конвейер, что демон запускает после
/// «Стоп» (rebuild_transcript.py сам разбирается, что уже сделано, и по
/// завершении зовёт graph_updater). Сборка вынесена в чистую функцию, чтобы
/// тест держал контракт: venv-питон, правильный скрипт, лог рядом с логом
/// демонского запуска той же встречи.
enum MeetingRetryCommand {
    static func build(root: URL, transcriptPath: String) -> (exec: URL, args: [String], log: URL) {
        let python = root.appendingPathComponent(".venv/bin/python").path
        let script = root.appendingPathComponent("src/rebuild_transcript.py").path
        let stem = URL(fileURLWithPath: transcriptPath)
            .deletingPathExtension().lastPathComponent
        let stamp = String(stem.prefix(15))
        return (
            exec: URL(fileURLWithPath: "/usr/bin/nice"),
            args: ["-n", "10", python, script, transcriptPath],
            log: root.appendingPathComponent("logs/graph_\(stamp).log")
        )
    }
}

/// Reads the Python pipeline's atomic status files without blocking SwiftUI.
///
/// The status is the source of truth: the app does not guess completion from a
/// timer. A timer is used only to notice a file change or declare a process
/// stale after thirty minutes.
@MainActor
final class MeetingProcessingService: ObservableObject {
    static let shared = MeetingProcessingService()

    @Published private(set) var snapshot: MeetingProcessingSnapshot?
    @Published private(set) var waitingForPipeline = false

    /// Конвейер не записал ни одного статуса за отведённое время.
    @Published private(set) var pipelineSilent = false

    /// Повторный запуск не записал ни статуса, ни процесса (нет venv, прав).
    @Published private(set) var retryFailedToStart = false

    /// Повтор уже идёт: кнопка недоступна, второй запуск не начнётся.
    @Published private(set) var retryInFlight = false

    /// Недавние встречи, новая первая. Наполняется тем же опросом статусов,
    /// что и текущее состояние: отдельного хранилища у истории нет —
    /// источник правды один, файлы конвейера.
    @Published private(set) var history: [MeetingProcessingSnapshot] = []

    private var timer: Timer?
    private var refreshInFlight = false
    private var waitingSince: Date?
    /// Не nil — ждём статус повторной обработки именно этой встречи.
    private var retryExpectation: RetryExpectation?

    /// Какая встреча повторяется прямо сейчас — для списка.
    ///
    /// Пока повтор шёл, кнопка «Повторить» появлялась погашенной у ВСЕХ строк,
    /// включая готовые встречи: список знал только факт «идёт повтор», но не
    /// знал чей. Готовым встречам этот факт не касается вовсе, а у повторяемой
    /// нужен не выключатель, а признак «работаю».
    var retryingID: String? {
        retryInFlight ? retryExpectation?.meetingID : nil
    }
    /// Процесс повтора, пока он жив. Держим ссылку, чтобы не запустить второй
    /// поверх работающего: два конвейера на одну встречу пишут один статус и
    /// один лог, и чей результат окажется последним — вопрос удачи.
    private var retryProcess: Process?
    private let notifiedKey = "charoite.processing.lastReadyNotification"

    private init() {}

    func startMonitoring() {
        guard timer == nil else { return }
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in self?.refresh() }
        }
    }

    /// Immediately replace the previous meeting's result after Stop is clicked.
    func expectResult() {
        waitingSince = Date()
        retryExpectation = nil
        waitingForPipeline = true
        pipelineSilent = false
        retryFailedToStart = false
        snapshot = nil
        refresh()
    }

    /// Есть ли у последней встречи ошибка, которую можно исправить повтором.
    ///
    /// Пока прошлый повтор жив — нет: иначе второе нажатие запустит второй
    /// конвейер поверх работающего. Это же условие гасит и «зависший»
    /// processing: тридцать минут без статуса делают состояние ошибкой, но
    /// процесс при этом может быть ещё жив.
    var canRetry: Bool {
        guard !retryInFlight else { return false }
        if let snapshot {
            return MeetingProcessingPolicy.canRetry(
                snapshot,
                transcriptExists: FileManager.default.fileExists(atPath: snapshot.transcriptPath))
        }
        // Повтор промолчал: снимка нет, но стенограмма известна из ожидания —
        // тупика быть не должно, пробовать снова можно.
        if pipelineSilent, let path = retryExpectation?.transcriptPath {
            return FileManager.default.fileExists(atPath: path)
        }
        return false
    }

    /// Что именно сломалось — строка конвейера, она уже человеческая
    /// («graph_updater завершился с кодом 1», «заметка встречи не создана»).
    var errorDetail: String? {
        guard let snapshot,
              MeetingProcessingPolicy.resolvedState(snapshot) == .error,
              let error = snapshot.error, !error.isEmpty else { return nil }
        return error
    }

    /// Запустить обработку заново по сохранённой стенограмме.
    ///
    /// Ошибка обработки не должна быть тупиком: стенограмма на диске, конвейер
    /// идемпотентен — но до этой кнопки повторить его можно было только из
    /// терминала, зная имя скрипта. Теперь тот же путь нажимается мышкой.
    func retry() {
        guard canRetry else { return }
        // путь берём из снимка, а если его нет — из ожидания промолчавшего
        // повтора; meetingID и отметка «свежее чего ждём» оттуда же
        let meetingID = snapshot?.meetingID ?? retryExpectation?.meetingID
        let path = snapshot?.transcriptPath ?? retryExpectation?.transcriptPath
        guard let meetingID, let path else { return }
        let seenAt = snapshot?.updatedAt ?? retryExpectation?.afterUpdatedAt ?? 0
        launchRetry(meetingID: meetingID, path: path, seenAt: seenAt)
    }

    /// Повторить обработку встречи, выбранной в списке.
    ///
    /// Это может быть не последняя встреча: позавчерашняя ошибка так же
    /// чинится, и заставлять ради неё «сделать её текущей» нечем.
    func retry(_ snapshot: MeetingProcessingSnapshot) {
        guard canRetry(snapshot) else { return }
        launchRetry(
            meetingID: snapshot.meetingID,
            path: snapshot.transcriptPath,
            seenAt: snapshot.updatedAt)
    }

    private func launchRetry(meetingID: String, path: String, seenAt: TimeInterval) {

        let cmd = MeetingRetryCommand.build(
            root: AppSettings.charoiteRoot,
            transcriptPath: path)

        let p = Process()
        p.executableURL = cmd.exec
        p.arguments = cmd.args
        p.currentDirectoryURL = AppSettings.charoiteRoot
        // лог — тот же файл, что у демонского запуска этой встречи, но append:
        // прошлый трейсбек — единственный след первой ошибки, затирать нельзя
        try? FileManager.default.createDirectory(
            at: cmd.log.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: cmd.log.path) {
            FileManager.default.createFile(atPath: cmd.log.path, contents: nil)
        }
        if let fh = try? FileHandle(forWritingTo: cmd.log) {
            fh.seekToEndOfFile()
            p.standardOutput = fh
            p.standardError = fh
        }
        // terminationHandler ставится ДО run(): короткий процесс (нет venv,
        // сразу упавший питон) успевает завершиться раньше следующей строки
        p.terminationHandler = { [weak self] proc in
            Task { @MainActor [weak self] in self?.retryFinished(proc) }
        }
        do {
            try p.run()
        } catch {
            retryFailedToStart = true
            return
        }
        retryProcess = p
        retryInFlight = true
        retryExpectation = RetryExpectation(
            meetingID: meetingID, afterUpdatedAt: seenAt, transcriptPath: path)
        waitingSince = Date()
        waitingForPipeline = true
        pipelineSilent = false
        retryFailedToStart = false
        self.snapshot = nil
        refresh()
    }

    /// Процесс повтора завершился.
    ///
    /// Ненулевой код — сразу честная ошибка: ждать три минуты «а вдруг статус
    /// появится» незачем, конвейер уже мёртв. Нулевой код ничего не решает:
    /// результат объявляет статус, а не код возврата, поэтому здесь только
    /// снимаем блокировку кнопки.
    private func retryFinished(_ proc: Process) {
        guard proc === retryProcess else { return }
        retryProcess = nil
        retryInFlight = false
        guard proc.terminationStatus != 0 else { return }
        if waitingForPipeline {
            waitingSince = nil
            waitingForPipeline = false
            retryFailedToStart = true
        }
    }

    var statusText: String? {
        if retryFailedToStart {
            return L.t("Не удалось запустить повторную обработку — проверьте logs/",
                       "Could not start reprocessing — check logs/",
                       "无法启动重新处理——请查看 logs/")
        }
        if waitingForPipeline {
            if retryExpectation != nil {
                return L.t("Повторяю обработку встречи…",
                           "Reprocessing the meeting…",
                           "正在重新处理会议…")
            }
            return L.t("Запускаю обработку встречи…",
                       "Starting meeting processing…",
                       "正在启动会议处理…")
        }
        if pipelineSilent {
            return L.t("Статус обработки не появился — стенограмма сохранена, проверьте logs/",
                       "Processing never reported status — the transcript was kept, check logs/",
                       "处理未上报状态——逐字稿已保留，请查看 logs/")
        }
        guard let snapshot else { return nil }
        switch MeetingProcessingPolicy.resolvedState(snapshot) {
        case .processing:
            return MeetingProcessingPolicy.stageText(for: snapshot)
        case .ready:
            return L.t("Встреча готова", "Meeting ready", "会议已就绪")
        case .error:
            let headline = snapshot.state == .processing
                ? L.t("Обработка не завершилась — стенограмма сохранена",
                      "Processing did not finish — the transcript was kept",
                      "处理未完成——逐字稿已保留")
                : L.t("Не удалось обработать встречу — стенограмма сохранена",
                      "Could not process the meeting — the transcript was kept",
                      "会议处理失败——逐字稿已保留")
            // причина — второй строкой: конвейер пишет её человеческим языком,
            // и оставлять её только в логах запрещает сам смысл этого статуса
            if let errorDetail {
                return headline + "\n" + errorDetail
            }
            return headline
        case .empty:
            return L.t("В записи нет речи — стенограмма пустая",
                       "No speech in the recording — the transcript is empty",
                       "录音中没有语音——逐字稿为空")
        case .unknown:
            return L.t("Статус встречи не распознан — проверьте logs/",
                       "Unrecognized meeting status — check logs/",
                       "无法识别会议状态——请查看 logs/")
        }
    }

    var isProcessing: Bool {
        waitingForPipeline || snapshot.map {
            MeetingProcessingPolicy.resolvedState($0) == .processing
        } == true
    }

    var isError: Bool {
        pipelineSilent || retryFailedToStart ||
            snapshot.map { MeetingProcessingPolicy.resolvedState($0) == .error } == true
    }

    var actionTitle: String? {
        guard let snapshot else { return nil }
        switch MeetingProcessingPolicy.resolvedState(snapshot) {
        case .ready:
            return L.t("Открыть встречу", "Open meeting", "打开会议")
        case .error, .empty:
            return L.t("Открыть стенограмму", "Open transcript", "打开逐字稿")
        case .processing, .unknown:
            return nil
        }
    }

    func openResult() {
        guard let snapshot else { return }
        open(snapshot)
    }

    /// Открыть результат конкретной встречи: готовую — заметкой, неудачную —
    /// стенограммой (она уцелела, и это единственное, что можно прочитать).
    func open(_ snapshot: MeetingProcessingSnapshot) {
        guard let path = MeetingProcessingPolicy.actionPath(for: snapshot),
              FileManager.default.fileExists(atPath: path) else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }

    /// Показать стенограмму встречи независимо от состояния: у готовой она
    /// тоже осталась, и иногда нужна именно она, а не заметка.
    func openTranscript(_ snapshot: MeetingProcessingSnapshot) {
        guard FileManager.default.fileExists(atPath: snapshot.transcriptPath) else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: snapshot.transcriptPath))
    }

    /// Можно ли повторить обработку этой встречи из списка.
    func canRetry(_ snapshot: MeetingProcessingSnapshot) -> Bool {
        guard !retryInFlight else { return false }
        return MeetingProcessingPolicy.canRetry(
            snapshot,
            transcriptExists: FileManager.default.fileExists(atPath: snapshot.transcriptPath))
    }

    private func refresh() {
        guard !refreshInFlight else { return }
        refreshInFlight = true
        let directory = AppSettings.charoiteRoot
            .appendingPathComponent("logs/meeting-status", isDirectory: true)
        Task { [weak self] in
            let snapshots = await Task.detached(priority: .utility) {
                Self.loadSnapshots(from: directory)
            }.value
            guard let self else { return }
            self.refreshInFlight = false
            self.history = MeetingProcessingPolicy.history(snapshots)
            self.accept(MeetingProcessingPolicy.latest(snapshots))
        }
    }

    private func accept(_ latest: MeetingProcessingSnapshot?) {
        if let waitingSince {
            if let latest, MeetingProcessingPolicy.matchesExpectation(
                latest, since: waitingSince, retry: retryExpectation) {
                self.waitingSince = nil
                retryExpectation = nil
                waitingForPipeline = false
                pipelineSilent = false
                snapshot = latest
            } else if MeetingProcessingPolicy.waitingExpired(since: waitingSince) {
                // Конвейер так и не объявил о себе — честная ошибка вместо
                // вечного «Запускаю…». Стенограмма при этом на диске, просто
                // статусов о ней не будет.
                self.waitingSince = nil
                // retryExpectation НЕ сбрасываем: в нём путь стенограммы, по
                // которому «Повторить» останется доступным и после молчания
                waitingForPipeline = false
                pipelineSilent = true
                snapshot = nil
            }
            // иначе продолжаем ждать первый статус
        } else {
            snapshot = latest
        }
        notifyIfReady()
    }

    private func notifyIfReady() {
        guard let snapshot,
              snapshot.state == .ready,
              snapshot.notePath != nil else { return }
        let defaults = UserDefaults.standard
        // ключ различает прогоны: после успешного повтора meetingID тот же,
        // но updated_at новый — «встреча готова» обязана прозвучать снова
        let stamp = "\(snapshot.meetingID)@\(Int(snapshot.updatedAt))"
        guard defaults.string(forKey: notifiedKey) != stamp else { return }
        defaults.set(stamp, forKey: notifiedKey)
        MeetingNotificationService.shared.presentReady(snapshot)
    }

    nonisolated private static func loadSnapshots(
        from directory: URL
    ) -> [MeetingProcessingSnapshot] {
        let files = (try? FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: nil,
            options: [.skipsSubdirectoryDescendants]
        )) ?? []
        let decoder = JSONDecoder()
        return files.compactMap { url in
            guard url.pathExtension == "json",
                  let data = try? Data(contentsOf: url) else { return nil }
            return try? decoder.decode(MeetingProcessingSnapshot.self, from: data)
        }
    }
}

#endif
