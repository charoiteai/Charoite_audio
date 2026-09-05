import AVFoundation
import XCTest
@testable import CharoiteiOS

/// «Слушать и записывать сразу» (№167): правила автостарта, взвода на занятом
/// микрофоне и просьбы интента — под тестом, а не под живым звонком.
final class RecorderAutoStartTests: XCTestCase {

    func testАвтостартТолькоНаХолодномЗапускеСВключённойНастройкой() {
        XCTAssertTrue(Recorder.shouldAutoStart(enabled: true, coldLaunch: true,
                                               isRecording: false, armed: false, deliveryReady: true))
        XCTAssertFalse(Recorder.shouldAutoStart(enabled: false, coldLaunch: true,
                                                isRecording: false, armed: false, deliveryReady: true),
                       "выключенная настройка — никакого автостарта")
        XCTAssertFalse(Recorder.shouldAutoStart(enabled: true, coldLaunch: false,
                                                isRecording: false, armed: false, deliveryReady: true),
                       "возврат из фона — не повод начать вторую запись")
        XCTAssertFalse(Recorder.shouldAutoStart(enabled: true, coldLaunch: true,
                                                isRecording: true, armed: false, deliveryReady: true))
        XCTAssertFalse(Recorder.shouldAutoStart(enabled: true, coldLaunch: true,
                                                isRecording: false, armed: true, deliveryReady: true),
                       "уже ждём микрофон — второй взвод не нужен")
        XCTAssertFalse(Recorder.shouldAutoStart(enabled: true, coldLaunch: true,
                                                isRecording: false, armed: false, deliveryReady: false),
                       "папка доставки не выбрана — первый запуск не пишет в никуда")
    }

    /// Взвод — не вечный: свёрнутое приложение стартовать не может, а через
    /// часы «посмотреть» не должно превращаться в запись (DS r1).
    func testВзводИстекаетЧерезПолчаса() {
        let t0 = Date(timeIntervalSince1970: 1_000_000)
        XCTAssertFalse(Recorder.armExpired(armedAt: t0, now: t0.addingTimeInterval(29 * 60)))
        XCTAssertTrue(Recorder.armExpired(armedAt: t0, now: t0.addingTimeInterval(31 * 60)))
        XCTAssertGreaterThanOrEqual(Recorder.armLifetime, 5 * 60)
        XCTAssertLessThanOrEqual(Recorder.armLifetime, 2 * 3600)
    }

    /// Баннер называет настоящую причину, а не всегда «звонок» (GLM/DS r1).
    func testСообщениеВзводаНазываетПричину() {
        let call = Recorder.armMessage(for: .sessionBusy)
        let other = Recorder.armMessage(for: .recorderBusy)
        XCTAssertTrue(call.contains(L.t("звонок", "call", "通话")), call)
        XCTAssertTrue(other.contains(L.t("другим приложением", "Another app", "其他应用")), other)
        XCTAssertNotEqual(call, other)
        for text in [call, other] {
            XCTAssertTrue(text.contains(L.t("пока приложение открыто", "while the app is open", "保持应用打开")),
                          "обещание сужено до открытого приложения: \(text)")
        }
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
        // Тесты hosted: у приложения-хоста свой обработчик — вернуть как было,
        // чтобы просьба из теста не стартовала настоящую запись (GLM M9)
        let saved = RecordingControl.onStart
        defer { RecordingControl.onStart = saved }
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
    }
}
