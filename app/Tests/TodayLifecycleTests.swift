import XCTest
@testable import CharoiteApp

/// Фазы «Сегодня» — чистая политика (ревью 15.08): главный кейс — готовый
/// результат висит сутки (MeetingProcessingPolicy) и не должен прятать
/// кнопку следующей записи с домашнего экрана.
final class TodayLifecycleTests: XCTestCase {
    private func phase(_ rec: RecordingLifecycle = .idle,
                       processing: Bool = false, ready: Bool = false)
        -> TodayWorkspaceView.LifecyclePhase {
        TodayWorkspaceView.lifecyclePhase(recording: rec,
                                          isProcessing: processing,
                                          hasReadyResult: ready)
    }

    func testIdleShowsRecord() {
        XCTAssertEqual(phase(), .record)
    }

    func testReadyResultStillOffersNextRecording() {
        XCTAssertEqual(phase(ready: true), .readyPlusRecord)
    }

    func testProcessingHidesCapsule() {
        XCTAssertEqual(phase(processing: true), .processing)
        // конвейер + готовый прошлый результат: обработка главнее
        XCTAssertEqual(phase(processing: true, ready: true), .processing)
    }

    func testRecordingShowsStop() {
        XCTAssertEqual(phase(.recording), .recording)
        // запись при готовом прошлом результате — всё ещё запись
        XCTAssertEqual(phase(.recording, ready: true), .recording)
    }

    func testTransitionsCarryDirection() {
        // направление из машины состояний: при остановке isRunning уже
        // false, и старая сигнатура показывала «Запускаю…» (ревью 15.08)
        XCTAssertEqual(phase(.starting), .transitioning(stopping: false))
        XCTAssertEqual(phase(.stopping), .transitioning(stopping: true))
        XCTAssertEqual(phase(.stopping, processing: true, ready: true),
                       .transitioning(stopping: true))
    }
}
