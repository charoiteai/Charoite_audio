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
    /// в первые секунды; три минуты тишины значят, что статус уже не появится.
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

    private var timer: Timer?
    private var refreshInFlight = false
    private var waitingSince: Date?
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
        waitingForPipeline = true
        pipelineSilent = false
        snapshot = nil
        refresh()
    }

    var statusText: String? {
        if waitingForPipeline {
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
            if snapshot.state == .processing {
                return L.t("Обработка не завершилась — стенограмма сохранена",
                           "Processing did not finish — the transcript was kept",
                           "处理未完成——逐字稿已保留")
            }
            return L.t("Не удалось обработать встречу — стенограмма сохранена",
                       "Could not process the meeting — the transcript was kept",
                       "会议处理失败——逐字稿已保留")
        }
    }

    var isProcessing: Bool {
        waitingForPipeline || snapshot.map {
            MeetingProcessingPolicy.resolvedState($0) == .processing
        } == true
    }

    var isError: Bool {
        pipelineSilent ||
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
            if let latest, latest.startedAt >= waitingSince.timeIntervalSince1970 - 5 {
                self.waitingSince = nil
                waitingForPipeline = false
                pipelineSilent = false
                snapshot = latest
            } else if MeetingProcessingPolicy.waitingExpired(since: waitingSince) {
                // Конвейер так и не объявил о себе — честная ошибка вместо
                // вечного «Запускаю…». Стенограмма при этом на диске, просто
                // статусов о ней не будет.
                self.waitingSince = nil
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
        guard defaults.string(forKey: notifiedKey) != snapshot.meetingID else { return }
        defaults.set(snapshot.meetingID, forKey: notifiedKey)
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
