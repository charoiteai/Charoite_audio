import Foundation
import XCTest
@testable import CharoiteApp

/// End-to-end contract at the Python NDJSON → Swift policy seam.
///
/// Watchdog tests prove that silence eventually restarts the daemon. These
/// tests prove that the measurements which do arrive cannot be discarded or
/// cleared by an unrelated status while a live meeting is degraded.
final class PipelineHealthTests: XCTestCase {
    private func object(_ json: String) throws -> [String: Any] {
        let value = try JSONSerialization.jsonObject(with: Data(json.utf8))
        return try XCTUnwrap(value as? [String: Any])
    }

    private func progress(
        state: String = "healthy",
        backlog: Double = 0.4,
        recordingOK: Bool = true
    ) throws -> [String: Any] {
        try object("""
        {
          "type": "stt_progress",
          "state": "\(state)",
          "stage": "idle",
          "stage_age_seconds": 0.1,
          "backlog_seconds": \(backlog),
          "input_age_seconds": 0.2,
          "cycle_ms": 720,
          "diarization_ms": 110,
          "transcription_ms": 590,
          "recording_ok": \(recordingOK),
          "channels": {
            "mic": {
              "backlog_seconds": \(backlog),
              "input_age_seconds": 0.2,
              "recording": true
            },
            "system": {
              "backlog_seconds": 0.1,
              "input_age_seconds": null,
              "recording": \(recordingOK)
            }
          }
        }
        """)
    }

    private func heartbeat(
        stage: String = "transcription",
        age: Double,
        stalled: Bool,
        recordingOK: Bool? = nil
    ) throws -> [String: Any] {
        let recordingField = recordingOK.map { ",\n  \"recording_ok\": \($0)" } ?? ""
        return try object("""
        {
          "type": "hb",
          "stt_stage": "\(stage)",
          "stt_stage_age_seconds": \(age),
          "stt_stalled": \(stalled)\(recordingField)
        }
        """)
    }

    func testPythonProgressContractDecodesActionableFields() throws {
        let snapshot = try XCTUnwrap(
            PipelineProgressSnapshot.decode(progress()))

        XCTAssertEqual(snapshot.state, .healthy)
        XCTAssertEqual(snapshot.backlogSeconds, 0.4)
        XCTAssertTrue(snapshot.recordingOK)
        XCTAssertTrue(snapshot.failedRecordingChannels.isEmpty)
    }

    func testLagRemainsAProblemUntilHealthyProgressArrives() throws {
        var monitor = PipelineHealthMonitor()
        monitor.acceptProgress(try progress(state: "lagging", backlog: 18.2))

        XCTAssertEqual(monitor.problem, .lagging(backlogSeconds: 18.2))

        // A normal main-thread heartbeat cannot pretend the STT queue caught up.
        monitor.acceptHeartbeat(try heartbeat(age: 1, stalled: false))
        XCTAssertEqual(monitor.problem, .lagging(backlogSeconds: 18.2))

        monitor.acceptProgress(try progress())
        XCTAssertNil(monitor.problem)
    }

    func testMainThreadReportsNativeStallWithoutBecomingSTTProgress() throws {
        var monitor = PipelineHealthMonitor()
        monitor.acceptProgress(try progress())
        monitor.acceptHeartbeat(try heartbeat(age: 61.4, stalled: true))

        XCTAssertEqual(
            monitor.problem,
            .stalled(stage: "transcription", seconds: 61.4))

        // A real STT event proves the consumer moved and clears the old probe.
        monitor.acceptProgress(try progress())
        XCTAssertNil(monitor.problem)
    }

    func testDiskFailureOutranksLagAndStallUntilConfirmedRecovery() throws {
        var monitor = PipelineHealthMonitor()
        monitor.acceptProgress(try progress(
            state: "lagging", backlog: 24, recordingOK: false))
        monitor.acceptHeartbeat(try heartbeat(age: 65, stalled: true))

        XCTAssertEqual(
            monitor.problem,
            .recordingUnavailable(channels: ["system"]))

        // Heartbeats and transient status messages know nothing about sinks;
        // only a fresh, valid progress snapshot may clear this critical state.
        monitor.acceptHeartbeat(try heartbeat(age: 1, stalled: false))
        XCTAssertEqual(
            monitor.problem,
            .recordingUnavailable(channels: ["system"]))

        monitor.acceptProgress(try progress())
        XCTAssertNil(monitor.problem)
    }

    func testMalformedProgressDoesNotEraseLastKnownFailure() throws {
        var monitor = PipelineHealthMonitor()
        monitor.acceptProgress(try progress(recordingOK: false))
        var malformed = try progress()
        malformed.removeValue(forKey: "recording_ok")

        XCTAssertNil(monitor.acceptProgress(malformed))
        XCTAssertEqual(
            monitor.problem,
            .recordingUnavailable(channels: ["system"]))
    }

    func testHeartbeatRaisesDiskFailureWhileSTTIsFrozen() throws {
        // recording_ok из stt_progress замерзает вместе с STT; свежий вердикт
        // о диске несёт heartbeat главного потока (круг-1 GLM, I1). Каналов
        // hb не знает — критикал поднимается с пустым списком.
        var monitor = PipelineHealthMonitor()
        monitor.acceptProgress(try progress())
        monitor.acceptHeartbeat(try heartbeat(age: 65, stalled: true,
                                              recordingOK: false))

        XCTAssertEqual(monitor.problem, .recordingUnavailable(channels: []))

        // Гасит критикал по-прежнему только валидный progress-снимок.
        monitor.acceptHeartbeat(try heartbeat(age: 1, stalled: false))
        XCTAssertEqual(monitor.problem, .recordingUnavailable(channels: []))

        monitor.acceptProgress(try progress())
        XCTAssertNil(monitor.problem)
    }

    func testPresentationNamesFailedChannelsAndTranslatesIdle() {
        let named = PipelineHealthPresentation.text(
            for: .recordingUnavailable(channels: ["system"]))
        XCTAssertTrue(named.contains("(system)"))

        let bare = PipelineHealthPresentation.text(
            for: .recordingUnavailable(channels: []))
        XCTAssertFalse(bare.contains("()"))

        // Сырой идентификатор стадии не утекает в текст для человека.
        let idle = PipelineHealthPresentation.text(
            for: .stalled(stage: "idle", seconds: 45))
        XCTAssertFalse(idle.contains("(idle)"))
    }

    func testHealthOwnerIsWiredToBothMeetingSurfaces() throws {
        // Pure policy tests are insufficient if consume keeps throwing the
        // event away or one of the two everyday surfaces never reads it.
        let app = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let sources = app.appendingPathComponent("Sources/CharoiteApp")
        let service = try String(contentsOf:
            sources.appendingPathComponent("Services/SuflerService.swift"),
            encoding: .utf8)
        let meeting = try String(contentsOf:
            sources.appendingPathComponent("Views/Sufler/SuflerView.swift"),
            encoding: .utf8)
        let menu = try String(contentsOf:
            sources.appendingPathComponent("Views/MenuBar/MenuBarView.swift"),
            encoding: .utf8)

        XCTAssertTrue(service.contains("case \"stt_progress\":"))
        XCTAssertTrue(service.contains("case \"hb\":"))
        XCTAssertTrue(service.contains("nextHealth.acceptProgress(obj)"))
        XCTAssertTrue(service.contains("nextHealth.acceptHeartbeat(obj)"))
        XCTAssertTrue(meeting.contains("sufler.pipelineStatusText"))
        XCTAssertTrue(menu.contains("sufler.pipelineStatusText"))
    }
}
