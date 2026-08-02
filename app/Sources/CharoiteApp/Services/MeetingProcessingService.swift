import Foundation

#if os(macOS)
import AppKit

struct MeetingProcessingSnapshot: Decodable, Equatable, Sendable {
    enum State: String, Decodable, Sendable {
        case processing
        case ready
        case error
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
        case .error:
            return snapshot.transcriptPath
        case .processing:
            return nil
        }
    }

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

    private var timer: Timer?
    private var refreshInFlight = false
    private var waitingSince: Date?
    /// Не nil — ждём статус повторной обработки именно этой встречи.
    private var retryExpectation: RetryExpectation?
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
            switch snapshot.stage {
            case "waiting_for_audio", "recovering":
                return L.t("Сохраняю запись…", "Saving the recording…", "正在保存录音…")
            case "rebuilding_transcript":
                return L.t("Пересобираю стенограмму…",
                           "Rebuilding the transcript…",
                           "正在重建逐字稿…")
            case "updating_graph":
                return L.t("Обновляю граф встречи…",
                           "Updating the meeting graph…",
                           "正在更新会议图谱…")
            default:
                return L.t("Обрабатываю встречу…",
                           "Processing the meeting…",
                           "正在处理会议…")
            }
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
        case .error:
            return L.t("Открыть стенограмму", "Open transcript", "打开逐字稿")
        case .processing:
            return nil
        }
    }

    func openResult() {
        guard let snapshot,
              let path = MeetingProcessingPolicy.actionPath(for: snapshot),
              FileManager.default.fileExists(atPath: path) else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
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
