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

    func testTimeoutStaysInSaneRangeAgainstHeartbeatCadence() {
        // Все граничные тесты выше — относительные (timeout ± 1): сдвиг
        // константы до 35 устроил бы рестарт-шторм против 30-секундного hb,
        // до 10⁵ — умертвил watchdog, и всё оставалось бы зелёным (ревью
        // 21.08, GLM). Литералы держат порядок величины.
        XCTAssertGreaterThan(PipelineWatchdog.timeout, 60)
        XCTAssertLessThan(PipelineWatchdog.timeout, 180)
    }

    func testSleepBetweenTicksIsDetectedByClockDivergence() {
        // Мак спал: стенные часы ушли вперёд, uptime стоял (ревью 21.08,
        // DeepSeek — после сна >100с все стенные якоря читались как «завис»
        // и здоровый демон улетал в рестарт, а с трёх снов — в giveUp).
        XCTAssertTrue(PipelineWatchdog.sleptBetweenTicks(
            wallDelta: 300, uptimeDelta: 2))
        // Обычный бодрый тик: часы идут вместе.
        XCTAssertFalse(PipelineWatchdog.sleptBetweenTicks(
            wallDelta: 31, uptimeDelta: 30))
        // Долгий тик без сна (машина занята) — не сон.
        XCTAssertFalse(PipelineWatchdog.sleptBetweenTicks(
            wallDelta: 95, uptimeDelta: 90))
    }
}
