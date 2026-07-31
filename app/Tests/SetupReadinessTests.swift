import XCTest
@testable import CharoiteApp

final class SetupReadinessTests: XCTestCase {
    func testModelTagsMatchExplicitAndLatestNames() {
        XCTAssertTrue(SetupReadinessPolicy.modelAvailable(
            "bge-m3:latest", in: ["bge-m3:latest"]))
        XCTAssertFalse(SetupReadinessPolicy.modelAvailable(
            "qwen3.5:4b", in: ["qwen3.5:latest"]))
        XCTAssertTrue(SetupReadinessPolicy.modelAvailable(
            "qwen3.5", in: ["qwen3.5:4b"]))
        XCTAssertFalse(SetupReadinessPolicy.modelAvailable(
            "qwen3.5:4b", in: ["gemma4:latest"]))
    }

    func testMissingModelsAreDeduplicated() {
        XCTAssertEqual(
            SetupReadinessPolicy.missingModels(
                ["qwen3.5:4b", "qwen3.5:4b", "gemma4:latest"],
                installed: ["gemma4:latest"]),
            ["qwen3.5:4b"])
    }

    func testSystemAudioRequiresActualBlackHoleInput() {
        XCTAssertTrue(SetupReadinessPolicy.hasSystemAudioInput(
            ["MacBook Microphone", "BlackHole 2ch"]))
        XCTAssertFalse(SetupReadinessPolicy.hasSystemAudioInput(
            ["MacBook Microphone", "External USB Mic"]))
        XCTAssertTrue(SetupReadinessPolicy.hasMicrophoneInput(
            ["MacBook Microphone", "BlackHole 2ch"]))
        XCTAssertFalse(SetupReadinessPolicy.hasMicrophoneInput(
            ["BlackHole 2ch"]))
    }

    func testWarningsDoNotBlockAFirstMeeting() {
        let snapshot = SetupReadinessSnapshot(checks: [
            SetupCheck(id: "audio", state: .warning, title: "audio", detail: "mic only"),
            SetupCheck(id: "graph", state: .warning, title: "graph", detail: "off"),
        ])
        XCTAssertTrue(snapshot.canStart)
        XCTAssertEqual(snapshot.warnings, 2)
        XCTAssertEqual(snapshot.problems, 0)
    }

    func testAnyBlockingProblemDisablesStart() {
        let snapshot = SetupReadinessSnapshot(checks: [
            SetupCheck(id: "python", state: .blocked, title: "python", detail: "missing"),
            SetupCheck(id: "audio", state: .ready, title: "audio", detail: "ready"),
        ])
        XCTAssertFalse(snapshot.canStart)
        XCTAssertEqual(snapshot.problems, 1)
    }
}
