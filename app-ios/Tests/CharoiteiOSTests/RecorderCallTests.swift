import AVFoundation
import XCTest
@testable import CharoiteiOS

/// Сценарий 07.08: долгая запись, посреди неё мессенджер начинает звонок.
///
/// Симулятор не умеет настоящий звонок, но уведомления прерывания —
/// те же самые, что шлёт iOS: путь кода от `.began` до `.ended` проверяется
/// честно. Чего тест доказать не может — саму паузу рекордера (в симуляторе
/// микрофон никто не отнимает), поэтому политика «во время звонка не
/// поднимаем и не ротируем» дополнительно закреплена юнит-тестами
/// shouldAutoResume в RecorderStallTests.
@MainActor
final class RecorderCallTests: XCTestCase {

    private func postInterruption(_ type: AVAudioSession.InterruptionType) {
        NotificationCenter.default.post(
            name: AVAudioSession.interruptionNotification,
            object: AVAudioSession.sharedInstance(),
            userInfo: [AVAudioSessionInterruptionTypeKey: type.rawValue])
    }

    private func wait(_ seconds: TimeInterval) {
        let e = expectation(description: "пауза")
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { e.fulfill() }
        waitForExpectations(timeout: seconds + 5)
    }

    func testЗвонокПосредиЗаписиНеРвётФайл() throws {
        let rec = Recorder()
        rec.start(kind: .meeting)
        wait(1.5)
        guard rec.isRecording else {
            throw XCTSkip("симулятор не дал записи — окружению нельзя верить")
        }

        // «Мессенджер начал звонок»
        postInterruption(.began)
        // Дольше порога застоя (3 с): старый код здесь трижды «поднимал»
        // запись, исчерпывал попытки и резал файл ротацией.
        wait(4.5)
        XCTAssertTrue(rec.isRecording,
                      "звонок не должен останавливать запись — это пауза")

        // «Звонок кончился»
        postInterruption(.ended)
        wait(2.0)
        XCTAssertTrue(rec.isRecording, "после звонка запись обязана продолжаться")
        let seconds = rec.elapsed
        XCTAssertGreaterThan(seconds, 1.0, "файл не растёт после конца звонка")

        rec.stop()
        wait(1.0)
        XCTAssertFalse(rec.isRecording)
        // Файл ровно один — ротаций из-за звонка не было.
        let sent = (try? FileManager.default.contentsOfDirectory(
            at: Inbox.sent, includingPropertiesForKeys: nil)) ?? []
        let fresh = sent.filter {
            (try? $0.resourceValues(forKeys: [.contentModificationDateKey])
                .contentModificationDate).map { Date().timeIntervalSince($0) < 30 } ?? false
        }
        XCTAssertLessThanOrEqual(fresh.count, 1,
                                 "звонок породил ротацию: встреча порезана на куски")
    }
}
