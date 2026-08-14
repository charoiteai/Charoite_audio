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
        XCTAssertTrue(gate.finishStop(stop))
        XCTAssertEqual(gate.state, .idle)
    }

    func testRecordingMustFinishStopBeforeNextStart() throws {
        var gate = RecordingLifecycleGate()
        let start = try XCTUnwrap(gate.beginStart())
        XCTAssertTrue(gate.markRecording(start))
        XCTAssertEqual(gate.state, .recording)

        let stop = try XCTUnwrap(gate.beginStop())
        XCTAssertNil(gate.beginStart(), "новая встреча не стартует во время teardown прошлой")
        XCTAssertTrue(gate.finishStop(stop))

        XCTAssertNotNil(gate.beginStart(), "после полного teardown следующая встреча разрешена")
    }

    func testStaleStopCannotFinishAnotherTransition() throws {
        var gate = RecordingLifecycleGate()
        _ = try XCTUnwrap(gate.beginStart())
        let firstStop = try XCTUnwrap(gate.beginStop())
        XCTAssertTrue(gate.finishStop(firstStop))

        _ = try XCTUnwrap(gate.beginStart())
        let secondStop = try XCTUnwrap(gate.beginStop())
        XCTAssertFalse(gate.finishStop(firstStop))
        XCTAssertTrue(gate.owns(secondStop, in: .stopping))
    }
}
