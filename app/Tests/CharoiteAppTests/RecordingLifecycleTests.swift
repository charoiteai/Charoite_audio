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

/// Застрявший демон не должен запирать запись навсегда.
///
/// Ожидание смерти процесса было бесконечным: повтор каждые полсекунды без
/// предела. В норме SIGKILL на 12-й секунде решает всё, но если процесс
/// окажется в непрерываемом ожидании, приложение осталось бы в `stopping` —
/// кнопка мертва, новую встречу не начать до перезапуска. Потерять
/// возможность записывать хуже, чем оставить висящий процесс.
final class ShutdownWaitLimitTests: XCTestCase {
    func testWaitsWhileDaemonIsAlive() {
        XCTAssertTrue(SuflerService.shouldWaitForDaemon(alive: true, waits: 0))
        XCTAssertTrue(SuflerService.shouldWaitForDaemon(alive: true,
                                                        waits: SuflerService.maxShutdownWaits - 1))
    }

    func testDeadDaemonNeedsNoWaiting() {
        XCTAssertFalse(SuflerService.shouldWaitForDaemon(alive: false, waits: 0))
    }

    func testGivesUpAtTheLimit() {
        XCTAssertFalse(SuflerService.shouldWaitForDaemon(alive: true,
                                                         waits: SuflerService.maxShutdownWaits),
                       "бесконечное ожидание запирает запись до перезапуска приложения")
    }

    func testLimitOutlivesTheSigkill() {
        // SIGKILL уходит на 12-й секунде, шаг ожидания — полсекунды. Предел
        // обязан быть заметно больше, иначе сдадимся раньше, чем система
        // добьёт процесс, и получим два демона на одну встречу.
        XCTAssertGreaterThan(Double(SuflerService.maxShutdownWaits) * 0.5, 12.0)
    }
}
