import XCTest
@testable import CharoiteApp

final class RecordingLifecycleTests: XCTestCase {

    func testDoubleStartIsSingleFlight() throws {
        var gate = RecordingLifecycleGate()
        let first = try XCTUnwrap(gate.beginStart())

        XCTAssertNil(gate.beginStart(), "второй Start не должен создавать ещё одну попытку")
        XCTAssertTrue(gate.owns(first, in: .starting))
        XCTAssertEqual(gate.state, .starting)
    }

    func testStopInvalidatesPendingStartCompletion() throws {
        var gate = RecordingLifecycleGate()
        let start = try XCTUnwrap(gate.beginStart())
        let stop = try XCTUnwrap(gate.beginStop())

        XCTAssertFalse(gate.markRecording(start),
                       "async completion старого Start не должен запустить daemon после Stop")
        XCTAssertTrue(gate.owns(stop, in: .stopping))
        XCTAssertTrue(gate.finishStop(stop, daemonAlive: false))
        XCTAssertEqual(gate.state, .idle)
    }

    func testRecordingMustFinishStopBeforeNextStart() throws {
        var gate = RecordingLifecycleGate()
        let start = try XCTUnwrap(gate.beginStart())
        XCTAssertTrue(gate.markRecording(start))
        XCTAssertEqual(gate.state, .recording)

        let stop = try XCTUnwrap(gate.beginStop())
        XCTAssertNil(gate.beginStart(), "новая встреча не стартует во время teardown прошлой")
        XCTAssertTrue(gate.finishStop(stop, daemonAlive: false))

        XCTAssertNotNil(gate.beginStart(), "после полного teardown следующая встреча разрешена")
    }

    func testStaleStopCannotFinishAnotherTransition() throws {
        var gate = RecordingLifecycleGate()
        _ = try XCTUnwrap(gate.beginStart())
        let firstStop = try XCTUnwrap(gate.beginStop())
        XCTAssertTrue(gate.finishStop(firstStop, daemonAlive: false))

        _ = try XCTUnwrap(gate.beginStart())
        let secondStop = try XCTUnwrap(gate.beginStop())
        XCTAssertFalse(gate.finishStop(firstStop, daemonAlive: false))
        XCTAssertTrue(gate.owns(secondStop, in: .stopping))
    }

    func testLiveDaemonCannotPublishIdle() throws {
        var gate = RecordingLifecycleGate()
        _ = try XCTUnwrap(gate.beginStart())
        let stop = try XCTUnwrap(gate.beginStop())

        XCTAssertFalse(gate.finishStop(stop, daemonAlive: true))
        XCTAssertEqual(gate.state, .stopping)
        XCTAssertNil(gate.beginStart())

        XCTAssertTrue(gate.finishStop(stop, daemonAlive: false))
        XCTAssertEqual(gate.state, .idle)
    }
}

/// Застрявший демон не должен превращаться в ложный idle.
///
/// После лимита частый polling прекращается, но lifecycle остаётся active до
/// настоящей смерти процесса. Иначе updater и quit не увидят живой daemon, а
/// UI разрешит Start, который всё равно будет отклонён fail-closed guard.
final class ShutdownWaitLimitTests: XCTestCase {
    func testRetriesWhileDaemonIsAliveBeforeLimit() {
        XCTAssertEqual(DaemonShutdownPolicy.action(alive: true, waits: 0), .retry)
        XCTAssertEqual(DaemonShutdownPolicy.action(
            alive: true,
            waits: DaemonShutdownPolicy.maxWaits - 1
        ), .retry)
    }

    func testDeadDaemonFinishesShutdown() {
        XCTAssertEqual(DaemonShutdownPolicy.action(alive: false, waits: 0), .finish)
    }

    func testLiveDaemonBlocksInsteadOfPublishingIdleAtLimit() {
        XCTAssertEqual(DaemonShutdownPolicy.action(
            alive: true,
            waits: DaemonShutdownPolicy.maxWaits
        ), .blocked, "живой daemon несовместим с idle даже после лимита polling")
    }

    func testStoppingStateIsActive() throws {
        var gate = RecordingLifecycleGate()
        _ = try XCTUnwrap(gate.beginStart())
        _ = try XCTUnwrap(gate.beginStop())

        XCTAssertEqual(gate.state, .stopping)
        XCTAssertTrue(RecordingLifecyclePolicy.isActive(gate.state, daemonAlive: false))
    }

    func testLiveDaemonOverridesIdleStateAndBlocksUpdate() {
        let active = RecordingLifecyclePolicy.isActive(.idle, daemonAlive: true)

        XCTAssertTrue(active, "живой daemon нельзя потерять даже при ошибочном idle")
        XCTAssertNotNil(UpdateService.refusalReason(
            recording: active,
            bundlePath: "/Applications/Charoite.app"
        ))
        XCTAssertFalse(RecordingLifecyclePolicy.isActive(.idle, daemonAlive: false))
    }

    func testShutdownPolicyAllowsFinishAfterLateDaemonDeath() throws {
        var gate = RecordingLifecycleGate()
        _ = try XCTUnwrap(gate.beginStart())
        let stop = try XCTUnwrap(gate.beginStop())

        XCTAssertEqual(DaemonShutdownPolicy.action(
            alive: true,
            waits: DaemonShutdownPolicy.maxWaits
        ), .blocked)
        XCTAssertEqual(gate.state, .stopping)
        XCTAssertFalse(gate.finishStop(stop, daemonAlive: true))

        XCTAssertEqual(DaemonShutdownPolicy.action(
            alive: false,
            waits: DaemonShutdownPolicy.maxWaits
        ), .finish)
        XCTAssertTrue(gate.finishStop(stop, daemonAlive: false))
        XCTAssertEqual(gate.state, .idle)
    }
}
