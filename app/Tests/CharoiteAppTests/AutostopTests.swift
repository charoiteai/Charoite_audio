import XCTest
@testable import CharoiteApp

/// Автостоп записи со стороны приложения.
///
/// Главный инвариант: после штатной остановки — в том числе автостопа по
/// тишине или потолку длительности — смерть демона НЕ поднимает запись заново.
/// Без него автостоп превратился бы в конвейер пустых встреч: демон уходит,
/// приложение считает это крахом, стартует новую запись, через пять минут
/// тишины она снова останавливается — и так по кругу.
final class AutostopTests: XCTestCase {

    func testNormalStopNeverRestartsRecording() {
        XCTAssertEqual(
            SuflerService.restartDecision(wasRecording: true, userStopped: true, attempts: 0),
            .none, "после нажатого Стоп/автостопа новая встреча не поднимается")
    }

    func testCrashDuringRecordingIsRecovered() {
        XCTAssertEqual(
            SuflerService.restartDecision(wasRecording: true, userStopped: false, attempts: 0),
            .restart, "оборванная запись должна восстанавливаться")
        XCTAssertEqual(
            SuflerService.restartDecision(wasRecording: true, userStopped: false, attempts: 2),
            .restart)
    }

    func testRecoveryGivesUpAfterThreeAttempts() {
        XCTAssertEqual(
            SuflerService.restartDecision(wasRecording: true, userStopped: false, attempts: 3),
            .giveUp, "краш-луп надо назвать вслух, а не крутить бесконечно")
    }

    func testStoppedStatusKeepsTheReason() {
        // Раньше причину затирало безусловное «Остановлен», и автостоп был
        // неотличим от собственного Стопа человека.
        XCTAssertTrue(SuflerService.stoppedStatus(autostopReason: "silence")
                        .contains("автоматически"))
        XCTAssertTrue(SuflerService.stoppedStatus(autostopReason: "limit")
                        .contains("потолок"))
        XCTAssertFalse(SuflerService.stoppedStatus(autostopReason: nil)
                        .contains("автоматически"), "ручной Стоп — обычный статус")
    }

    func testIdleDaemonDeathIsNotARecordingFailure() {
        XCTAssertEqual(
            SuflerService.restartDecision(wasRecording: false, userStopped: false, attempts: 0),
            .none)
    }
}

@MainActor
final class FinalStatusTests: XCTestCase {
    /// Закрытие по захвату: причина переживает финальные статусы демона
    /// (круг-5 по PR #383, Codex); кнопка — «Остановлен»/автостоп; перезапуск
    /// статус не трогает.
    func testPreserveFailureRestoresReason() {
        let final = SuflerService.finalStatus(disposition: .preserveFailure,
                                              preservedFailure: "Захват звука потерян снова",
                                              autostopReason: nil)
        XCTAssertEqual(final?.text, "Захват звука потерян снова")
        XCTAssertEqual(final?.isError, true)
    }

    func testPreserveFailureWithoutTextLeavesStatus() {
        XCTAssertNil(SuflerService.finalStatus(disposition: .preserveFailure,
                                               preservedFailure: nil, autostopReason: nil))
    }

    func testStoppedKeepsAutostopReasonAndRestartIsSilent() {
        let stopped = SuflerService.finalStatus(disposition: .stopped,
                                                preservedFailure: "не должно попасть",
                                                autostopReason: "silence")
        XCTAssertEqual(stopped?.text, SuflerService.stoppedStatus(autostopReason: "silence"))
        XCTAssertEqual(stopped?.isError, false)
        XCTAssertNil(SuflerService.finalStatus(disposition: .restart,
                                               preservedFailure: "x", autostopReason: nil))
    }
}
