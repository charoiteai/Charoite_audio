import Foundation

private func nonnegativeNumber(_ object: Any?) -> Double? {
    guard let number = object as? NSNumber else { return nil }
    let value = number.doubleValue
    return value.isFinite && value >= 0 ? value : nil
}

/// Typed view of the daemon's `stt_progress` contract.
///
/// The daemon already measures the live pipeline. Keeping the event as an
/// untyped `[String: Any]` in the app meant only its liveness/input-age anchor
/// survived; actionable state and `recording_ok=false` were discarded.
struct PipelineProgressSnapshot: Equatable {
    enum State: String, Equatable {
        case healthy
        case lagging
    }

    let state: State
    let backlogSeconds: TimeInterval
    let recordingOK: Bool
    let failedRecordingChannels: [String]

    static func decode(_ object: [String: Any]) -> PipelineProgressSnapshot? {
        guard let stateName = object["state"] as? String,
              let state = State(rawValue: stateName),
              let backlog = nonnegativeNumber(object["backlog_seconds"]),
              let recordingOK = object["recording_ok"] as? Bool
        else { return nil }

        let channelObjects = object["channels"] as? [String: Any] ?? [:]
        let failedChannels = channelObjects.compactMap { name, value -> String? in
            guard let channel = value as? [String: Any],
                  channel["recording"] as? Bool == false
            else { return nil }
            return name
        }.sorted()
        return PipelineProgressSnapshot(
            state: state,
            backlogSeconds: backlog,
            recordingOK: recordingOK,
            failedRecordingChannels: failedChannels)
    }
}

/// Probe emitted by the daemon's main thread while the STT thread may be
/// blocked inside native inference.  It must not count as STT progress: that
/// would let a live main thread hide a dead consumer from `PipelineWatchdog`.
struct PipelineStageProbe: Equatable {
    let stage: String
    let stageAgeSeconds: TimeInterval
    let stalled: Bool
    /// Свежий вердикт о записи от main-thread. `recording_ok` в
    /// `stt_progress` замерзает вместе с STT — ровно тогда, когда отказ
    /// диска важнее всего (круг-1 GLM, I1). nil — демон без поля.
    let recordingOK: Bool?
    /// Потребитель аудио (`_pump`): он пишет блоки каждого канала в файл и в
    /// STT-буфер, поэтому мёртвый насос — это остановленная запись, а не
    /// «гибнет потребитель». Оба поля приезжали в каждом hb с №311 и умирали
    /// на границе декодера (Critical DS входного круга по №139; №313).
    /// nil — демон без поля.
    let pumpAlive: Bool?
    /// Проходов потребителя, упавших подряд, на момент hb (0 — норма). Счётчик
    /// общий для блока канала и сторожа — потерю аудио по нему не утверждать.
    let pumpFailures: Int?

    init(stage: String, stageAgeSeconds: TimeInterval, stalled: Bool, recordingOK: Bool?,
         pumpAlive: Bool? = nil, pumpFailures: Int? = nil) {
        self.stage = stage
        self.stageAgeSeconds = stageAgeSeconds
        self.stalled = stalled
        self.recordingOK = recordingOK
        self.pumpAlive = pumpAlive
        self.pumpFailures = pumpFailures
    }

    static func decode(_ object: [String: Any]) -> PipelineStageProbe? {
        guard let stage = object["stt_stage"] as? String,
              let age = nonnegativeNumber(object["stt_stage_age_seconds"]),
              let stalled = object["stt_stalled"] as? Bool
        else { return nil }
        return PipelineStageProbe(stage: stage,
                                  stageAgeSeconds: age,
                                  stalled: stalled,
                                  recordingOK: object["recording_ok"] as? Bool,
                                  pumpAlive: object["pump_alive"] as? Bool,
                                  pumpFailures: nonnegativeNumber(object["pump_failures"]).map { Int($0) })
    }
}

enum PipelineHealthProblem: Equatable {
    case recordingUnavailable(channels: [String])
    /// Поток-потребитель аудио остановился: блоки не доходят ни до файла, ни до
    /// STT — запись стоит, хотя захват жив (№313).
    case pumpDead
    case stalled(stage: String, seconds: TimeInterval)
    /// Проходы потребителя падают подряд: часть блоков могла не дойти до файла.
    case pumpFailing(count: Int)
    case lagging(backlogSeconds: TimeInterval)

    /// Красный — только «данные гибнут»: отказ диска и мёртвый насос. Зависший
    /// STT, сбои проходов и отставание — жёлтые: аудио на диске есть.
    var isCritical: Bool {
        switch self {
        case .recordingUnavailable, .pumpDead: return true
        case .stalled, .pumpFailing, .lagging: return false
        }
    }
}

/// One presentation policy shared by the meeting window and menu bar.
/// Keeping these strings out of `SuflerService` avoids adding another concern
/// to the process/lifecycle owner and prevents the two surfaces from drifting.
enum PipelineHealthPresentation {
    static func text(for problem: PipelineHealthProblem) -> String {
        switch problem {
        case .recordingUnavailable(let channels):
            let scope = channels.isEmpty ? "" : " (\(channels.joined(separator: ", ")))"
            return L.t(
                "⛔️ Аудио не пишется на диск\(scope) — освободите место",
                "⛔️ Audio is not being saved\(scope) — free disk space",
                "⛔️ 音频未写入磁盘\(scope)——请释放磁盘空间")
        case .pumpDead:
            return L.t(
                "⛔️ Поток записи остановился — аудио не пишется, перезапустите запись",
                "⛔️ The recording thread stopped — audio is not being saved, restart the recording",
                "⛔️ 录音线程已停止——音频未写入，请重新开始录音")
        case .pumpFailing(let count):
            // счётчик демона считает любой упавший проход потребителя — и блок
            // канала, и сторож; о потере аудио говорит только `recording_ok`,
            // поэтому здесь факт владельца, не обещание потери (Important DS)
            return L.t(
                "⚠️ Сбои потока записи подряд: \(count) — проверьте logs/",
                "⚠️ Recording thread failures in a row: \(count) — check logs/",
                "⚠️ 录音线程连续失败 \(count) 次——请查看 logs/")
        case .stalled(let stage, let seconds):
            let age = Int(seconds.rounded(.up))
            let title = stageTitle(stage)
            return L.t(
                "⚠️ STT не отвечает \(age) с (\(title)) — аудио сохраняется",
                "⚠️ STT has not responded for \(age)s (\(title)) — audio is safe",
                "⚠️ STT 已 \(age) 秒无响应（\(title)）——音频仍在保存")
        case .lagging(let backlog):
            let seconds = Int(backlog.rounded(.up))
            return L.t(
                "⚠️ Стенограмма отстаёт на \(seconds) с — аудио сохраняется",
                "⚠️ Transcript is \(seconds)s behind — audio is safe",
                "⚠️ 逐字稿落后 \(seconds) 秒——音频仍在保存")
        }
    }

    private static func stageTitle(_ stage: String) -> String {
        switch stage {
        case "starting":
            return L.t("запуск", "startup", "启动")
        case "audio_pull":
            return L.t("аудиовход", "audio input", "音频输入")
        case "diarization":
            return L.t("разделение голосов", "speaker separation", "说话人分离")
        case "transcription":
            return L.t("распознавание", "transcription", "转写")
        case "planning", "postprocess":
            return L.t("обработка", "processing", "处理")
        case "idle":
            return L.t("ожидание речи", "waiting for speech", "等待语音")
        default:
            return stage
        }
    }
}

/// Joins the two existing health feeds without inventing another monitor.
/// A progress event proves the STT thread moved and clears an older stall
/// probe.  An ordinary UI status never touches this state, so a disk failure
/// or sustained lag remains visible until a valid recovery snapshot arrives.
struct PipelineHealthMonitor: Equatable {
    private(set) var progress: PipelineProgressSnapshot?
    private(set) var stageProbe: PipelineStageProbe?
    /// Липкий признак «hb сказал: запись не идёт». Каждый следующий hb
    /// замещает probe целиком, поэтому критикал, живущий в самом probe,
    /// гас бы через один такт (это поймал тест). Явный `recording_ok`
    /// ставит и снимает флаг, hb без поля (старый демон) его не трогает,
    /// валидный progress-снимок сбрасывает.
    private(set) var recordingFailedByHeartbeat = false

    var problem: PipelineHealthProblem? {
        // Отказ диска виден с обеих сторон шва: из снапшота STT-потока и из
        // heartbeat главного. Явный `recording_ok` в hb ставит И снимает
        // липкий флаг (снятие — серверное подтверждение восстановления);
        // hb без поля не трогает его, валидный progress-снимок сбрасывает
        // вместе с probe в acceptProgress (круг-2 DS, M2: коммент обязан
        // совпадать с кодом — снятие по hb уже реализовано).
        if let progress, !progress.recordingOK {
            return .recordingUnavailable(
                channels: progress.failedRecordingChannels)
        }
        if recordingFailedByHeartbeat {
            return .recordingUnavailable(
                channels: progress?.failedRecordingChannels ?? [])
        }
        // мёртвый насос — данные гибнут: выше зависшего STT; сбои проходов —
        // предупреждение, выше отставания (№313; ранг прибит тестом)
        if let stageProbe, stageProbe.pumpAlive == false {
            return .pumpDead
        }
        if let stageProbe, stageProbe.stalled {
            return .stalled(stage: stageProbe.stage,
                            seconds: stageProbe.stageAgeSeconds)
        }
        if let stageProbe, let failures = stageProbe.pumpFailures, failures > 0 {
            return .pumpFailing(count: failures)
        }
        if let progress, progress.state == .lagging {
            return .lagging(backlogSeconds: progress.backlogSeconds)
        }
        return nil
    }

    @discardableResult
    mutating func acceptProgress(
        _ object: [String: Any]
    ) -> PipelineProgressSnapshot? {
        guard let snapshot = PipelineProgressSnapshot.decode(object) else { return nil }
        progress = snapshot
        stageProbe = nil
        recordingFailedByHeartbeat = false
        return snapshot
    }

    @discardableResult
    mutating func acceptHeartbeat(
        _ object: [String: Any]
    ) -> PipelineStageProbe? {
        guard let probe = PipelineStageProbe.decode(object) else { return nil }
        stageProbe = probe
        if let recordingOK = probe.recordingOK {
            recordingFailedByHeartbeat = !recordingOK
        }
        return probe
    }
}

@MainActor
extension SuflerService {
    var pipelineStatusText: String? {
        guard isRunning, let problem = pipelineHealth.problem else { return nil }
        return PipelineHealthPresentation.text(for: problem)
    }

    var pipelineStatusIsCritical: Bool {
        isRunning && pipelineHealth.problem?.isCritical == true
    }
}
