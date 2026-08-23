import XCTest
@testable import CharoiteApp

/// Поток ScreenCaptureKit умирал посреди встречи, а `didStopWithError` только
/// логировал: на macOS 15 тем же потоком идёт микрофон, и встреча до конца
/// оставалась без обеих сторон (аудит DeepSeek 16.08, карточка №35).
///
/// Сама пересборка потока требует живого экрана; здесь — решение «когда и
/// сколько раз», вынесенное в чистую политику.
final class CaptureRestartPolicyTests: XCTestCase {

    private let t0 = Date(timeIntervalSince1970: 1_000_000)

    func testSystemStopRetriesWithExponentialBackoffThenGivesUp() {
        var p = CaptureRestartPolicy()
        var delays: [TimeInterval] = []
        for i in 0..<CaptureRestartPolicy.maxAttempts {
            guard case .retry(let after) = p.decide(userStopped: false, now: t0.addingTimeInterval(Double(i)))
            else { return XCTFail("попытка \(i + 1) должна быть повтором") }
            delays.append(after)
        }
        XCTAssertEqual(delays, [2, 4, 8, 16, 32, 60, 60, 60], "пауза растёт вдвое до потолка 60 с")
        guard case .giveUp(let reason) = p.decide(userStopped: false, now: t0.addingTimeInterval(600))
        else { return XCTFail("после потолка попыток — сдаёмся и говорим человеку") }
        XCTAssertTrue(reason.contains("попыток"), reason)
    }

    func testRecoveryResetsTheAttemptCounter() {
        var p = CaptureRestartPolicy()
        for i in 0..<3 { _ = p.decide(userStopped: false, now: t0.addingTimeInterval(Double(i))) }
        p.recovered()
        guard case .retry(let after) = p.decide(userStopped: false, now: t0.addingTimeInterval(100))
        else { return XCTFail() }
        XCTAssertEqual(after, 2, "после восстановления новый сбой считается с первой попытки")
    }

    /// Один «Стоп» в системном индикаторе — скорее всего случайный: наш захват
    /// не «шаринг», и человек не ждёт, что пропадёт запись. Второй за две
    /// минуты — честный, его уважаем.
    func testUserStopIsRetriedOnceThenRespected() {
        var p = CaptureRestartPolicy()
        XCTAssertEqual(p.decide(userStopped: true, now: t0), .retry(after: 1))
        guard case .giveUp(let reason) = p.decide(userStopped: true, now: t0.addingTimeInterval(30))
        else { return XCTFail("второй «Стоп» подряд — не повторяем") }
        XCTAssertTrue(reason.contains("человеком"), reason)
    }

    func testUserStopLongAfterThePreviousOneIsRetriedAgain() {
        var p = CaptureRestartPolicy()
        _ = p.decide(userStopped: true, now: t0)
        XCTAssertEqual(p.decide(userStopped: true,
                                now: t0.addingTimeInterval(CaptureRestartPolicy.userStopWindow + 1)),
                       .retry(after: 1))
    }

    /// Память о «Стопе» человека переживает восстановление: recovered()
    /// обнуляет только счёт технических попыток.
    func testRecoveryKeepsTheUserStopMemory() {
        var p = CaptureRestartPolicy()
        _ = p.decide(userStopped: true, now: t0)
        p.recovered()
        guard case .giveUp = p.decide(userStopped: true, now: t0.addingTimeInterval(10))
        else { return XCTFail("второй «Стоп» после восстановления всё ещё уважаем") }
    }
}
