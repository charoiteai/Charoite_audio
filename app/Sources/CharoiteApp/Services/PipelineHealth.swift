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
    let inputAgeSeconds: TimeInterval?
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
            inputAgeSeconds: nonnegativeNumber(object["input_age_seconds"]),
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

    static func decode(_ object: [String: Any]) -> PipelineStageProbe? {
        guard let stage = object["stt_stage"] as? String,
              let age = nonnegativeNumber(object["stt_stage_age_seconds"]),
              let stalled = object["stt_stalled"] as? Bool
        else { return nil }
        return PipelineStageProbe(stage: stage,
                                  stageAgeSeconds: age,
                                  stalled: stalled)
    }
}

enum PipelineHealthProblem: Equatable {
    case recordingUnavailable(channels: [String])
    case stalled(stage: String, seconds: TimeInterval)
    case lagging(backlogSeconds: TimeInterval)

    var isCritical: Bool {
        if case .recordingUnavailable = self { return true }
        return false
    }
}

/// One presentation policy shared by the meeting window and menu bar.
/// Keeping these strings out of `SuflerService` avoids adding another concern
/// to the process/lifecycle owner and prevents the two surfaces from drifting.
enum PipelineHealthPresentation {
    static func text(for problem: PipelineHealthProblem) -> String {
        switch problem {
        case .recordingUnavailable:
            return L.t(
                "⛔️ Аудио не пишется на диск — освободите место",
                "⛔️ Audio is not being saved — free disk space",
                "⛔️ 音频未写入磁盘——请释放磁盘空间")
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

    var problem: PipelineHealthProblem? {
        if let progress, !progress.recordingOK {
            return .recordingUnavailable(
                channels: progress.failedRecordingChannels)
        }
        if let stageProbe, stageProbe.stalled {
            return .stalled(stage: stageProbe.stage,
                            seconds: stageProbe.stageAgeSeconds)
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
        return snapshot
    }

    @discardableResult
    mutating func acceptHeartbeat(
        _ object: [String: Any]
    ) -> PipelineStageProbe? {
        guard let probe = PipelineStageProbe.decode(object) else { return nil }
        stageProbe = probe
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
