import AVFoundation
import XCTest
@testable import CharoiteiOS

/// «Слушать и записывать сразу» (№167): правила автостарта, взвода на занятом
/// микрофоне и просьбы интента — под тестом, а не под живым звонком.
final class RecorderAutoStartTests: XCTestCase {

    func testАвтостартТолькоНаХолодномЗапускеСВключённойНастройкой() {
        XCTAssertTrue(Recorder.shouldAutoStart(enabled: true, coldLaunch: true,
                                               isRecording: false, armed: false))
        XCTAssertFalse(Recorder.shouldAutoStart(enabled: false, coldLaunch: true,
                                                isRecording: false, armed: false),
                       "выключенная настройка — никакого автостарта")
        XCTAssertFalse(Recorder.shouldAutoStart(enabled: true, coldLaunch: false,
                                                isRecording: false, armed: false),
                       "возврат из фона — не повод начать вторую запись")
        XCTAssertFalse(Recorder.shouldAutoStart(enabled: true, coldLaunch: true,
                                                isRecording: true, armed: false))
        XCTAssertFalse(Recorder.shouldAutoStart(enabled: true, coldLaunch: true,
                                                isRecording: false, armed: true),
                       "уже ждём микрофон — второй взвод не нужен")
    }

    func testВзводТолькоНаВременныеПричины() {
        XCTAssertTrue(Recorder.shouldArm(after: .sessionBusy), "звонок кончится — стартуем сами")
        XCTAssertTrue(Recorder.shouldArm(after: .recorderBusy), "чужое приложение отпустит вход")
        XCTAssertFalse(Recorder.shouldArm(after: .permissionDenied), "запрет ожиданием не лечится")
        XCTAssertFalse(Recorder.shouldArm(after: .lowStorage))
        XCTAssertFalse(Recorder.shouldArm(after: .other))
    }

    func testЗанятаяСессияЧитаетсяКакЗвонок() {
        let busy = NSError(domain: NSOSStatusErrorDomain,
                           code: AVAudioSession.ErrorCode.insufficientPriority.rawValue)
        XCTAssertEqual(Recorder.failure(for: busy), .sessionBusy)
        let other = NSError(domain: NSOSStatusErrorDomain, code: -50)
        XCTAssertEqual(Recorder.failure(for: other), .other)
    }

    func testПробаМикрофонаНеЧащеРазаВНесколькоСекунд() {
        // Проба — setActive + record на каждом тике; чаще пяти секунд это
        // уже дёрганье системы, реже тридцати — потерянные секунды встречи.
        XCTAssertGreaterThanOrEqual(Recorder.armProbeEvery, 2)
        XCTAssertLessThanOrEqual(Recorder.armProbeEvery, 30)
    }

    @MainActor
    func testПросьбаИнтентаЖдётЭкранЕслиЕгоЕщёНет() {
        RecordingControl.onStart = nil
        _ = RecordingControl.takeStartRequest()          // чистый стол
        RecordingControl.requestStart()
        XCTAssertTrue(RecordingControl.takeStartRequest(), "холодный запуск интентом — просьба ждёт экран")
        XCTAssertFalse(RecordingControl.takeStartRequest(), "просьба одноразовая")

        var fired = 0
        RecordingControl.onStart = { fired += 1 }
        RecordingControl.requestStart()
        XCTAssertEqual(fired, 1, "экран на месте — стартуем сразу")
        XCTAssertFalse(RecordingControl.takeStartRequest(), "исполненная просьба не откладывается")
        RecordingControl.onStart = nil
    }
}
