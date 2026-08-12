import AVFoundation
import XCTest
@testable import CharoiteiOS

/// Прерывание, о конце которого iOS не сообщила.
///
/// Фикс #253 сделал звонок паузой: пока `interrupted`, сторож застоя не
/// пытается поднять запись — и правильно, иначе три неудачных resume,
/// ротация и огрызок вместо встречи (07.08).
///
/// Но флаг снимался ТОЛЬКО по `.ended`, а его доставка не гарантирована —
/// это и в документации Apple, и в комментарии рядом в самом рекордере
/// («iOS далеко не всегда присылает `.ended`»). Не пришло — и запись стоит
/// вечно: таймер идёт, плашка на локскрине показывает запись, файл не
/// растёт. Человек узнаёт об этом после встречи (аудит 0.46.0, P0-9).
///
/// Лечение — редкая проба входа, отдельная от `tryResume()`: она не тратит
/// попытки и не ротирует файл, поэтому безопасна во время живого звонка.
final class RecorderLostEndedTests: XCTestCase {

    // MARK: - Когда пробовать

    func testNoProbeDuringFirstMinute() {
        // Короткий звонок закончится сам и пришлёт `.ended` штатно —
        // дёргать вход в первую минуту незачем.
        XCTAssertFalse(Recorder.shouldProbeInterruption(interruptedFor: 5,
                                                        sinceLastProbe: nil))
        XCTAssertFalse(Recorder.shouldProbeInterruption(interruptedFor: 59,
                                                        sinceLastProbe: nil))
    }

    func testFirstProbeAfterThreshold() {
        XCTAssertTrue(Recorder.shouldProbeInterruption(interruptedFor: 60,
                                                       sinceLastProbe: nil),
                      "минута прошла, `.ended` нет — пора проверить вход самим")
        XCTAssertTrue(Recorder.shouldProbeInterruption(interruptedFor: 600,
                                                       sinceLastProbe: nil))
    }

    func testProbesAreRareAfterTheFirst() {
        XCTAssertFalse(Recorder.shouldProbeInterruption(interruptedFor: 120,
                                                        sinceLastProbe: 5),
                       "пробовать на каждом такте не нужно: звонок идёт")
        XCTAssertFalse(Recorder.shouldProbeInterruption(interruptedFor: 120,
                                                        sinceLastProbe: 29))
        XCTAssertTrue(Recorder.shouldProbeInterruption(interruptedFor: 120,
                                                       sinceLastProbe: 30),
                      "раз в полминуты — достаточно, чтобы не потерять встречу")
    }

    /// Часовой «звонок» (забытая конференция) не должен усыплять пробы.
    func testLongInterruptionKeepsProbing() {
        XCTAssertTrue(Recorder.shouldProbeInterruption(interruptedFor: 3600,
                                                       sinceLastProbe: 45))
    }

    // MARK: - Проба не мешает политике паузы

    /// Во время прерывания обычный resume по-прежнему запрещён: он считает
    /// попытки и ротирует файл. Проба — отдельный, безопасный путь.
    func testAutoResumeStillBlockedWhileInterrupted() {
        XCTAssertFalse(Recorder.shouldAutoResume(stalled: true, interrupted: true),
                       "resume во время звонка рубит файл — это и чинил фикс #253")
        XCTAssertTrue(Recorder.shouldAutoResume(stalled: true, interrupted: false))
    }

    /// Сторож проводки: проба обязана вызываться из тика и не трогать
    /// счётчик попыток — иначе она превращается в тот самый resume.
    func testProbeIsWiredAndDoesNotConsumeAttempts() throws {
        let source = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/CharoiteiOS/Recorder.swift")
        let text = try String(contentsOf: source, encoding: .utf8)

        XCTAssertTrue(text.contains("probeInterruptionIfNeeded()"),
                      "проба написана, но не вызывается из тика — запись снова "
                    + "будет стоять вечно")

        guard let start = text.range(of: "private func probeInterruptionIfNeeded()"),
              let end = text.range(of: "\n    }", range: start.upperBound..<text.endIndex)
        else { return XCTFail("тело пробы не найдено — тест устарел") }
        let body = String(text[start.lowerBound..<end.upperBound])

        XCTAssertFalse(body.contains("resumeAttempts"),
                       "проба тратит лимит попыток: после трёх файл ротируется, "
                     + "и встреча режется прямо во время звонка")
        XCTAssertFalse(body.contains("rotateFile"),
                       "проба ротирует файл — это ровно тот вред, ради которого "
                     + "прерывание сделали паузой")
    }
}
