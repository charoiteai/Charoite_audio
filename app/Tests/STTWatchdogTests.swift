import XCTest
@testable import CharoiteApp

/// Main-thread heartbeat is not pipeline health: it kept arriving while the
/// sole STT consumer was dead.  These tests hold the three signals apart.
final class STTWatchdogTests: XCTestCase {
    func testFreshPipelineDoesNotRestart() {
        XCTAssertFalse(PipelineWatchdog.shouldRestart(
            daemonEventAge: 5,
            sttProgressAge: 6,
            audioInputAge: 1))
    }

    func testHeartbeatCannotHideStalledSTT() {
        XCTAssertTrue(PipelineWatchdog.shouldRestart(
            daemonEventAge: 1,
            sttProgressAge: PipelineWatchdog.timeout + 1,
            audioInputAge: 1))
    }

    func testSTTHeartbeatCannotHideDeadCapture() {
        XCTAssertTrue(PipelineWatchdog.shouldRestart(
            daemonEventAge: 1,
            sttProgressAge: 1,
            audioInputAge: PipelineWatchdog.timeout + 1))
    }

    func testMissingOptionalSignalsBeforeFirstProgressUseDaemonHeartbeat() {
        XCTAssertFalse(PipelineWatchdog.shouldRestart(
            daemonEventAge: 1,
            sttProgressAge: nil,
            audioInputAge: nil))
        XCTAssertTrue(PipelineWatchdog.shouldRestart(
            daemonEventAge: PipelineWatchdog.timeout + 1,
            sttProgressAge: nil,
            audioInputAge: nil))
    }
}
