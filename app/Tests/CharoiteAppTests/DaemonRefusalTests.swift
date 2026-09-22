import XCTest
@testable import CharoiteApp

/// Исход «сдаёмся» проигрывается на живом сервисе, а не только на чистой
/// функции текста: три правки в теле `giveUp` — потеря причины захвата,
/// снятие `preservedFailure`, снятие стража сна — проходили зелёными, потому
/// что до `giveUp` не доходил ни один тест (круг 5 по коду №332, DS I2).
/// Из `idle` гейт остановки отвечает `nil`, и метод возвращается сразу после
/// установки статуса — этого достаточно, чтобы судить о том, что видит человек.
@MainActor
final class DaemonRefusalTests: XCTestCase {
    func testGiveUpAfterAttemptsKeepsTheCaptureLossReasonOnScreen() {
        let s = SuflerService()
        s.captureLossReason = "устройство"
        s.beginSleepGuard()
        s.giveUp(.giveUp)
        XCTAssertFalse(s.sleepGuardActive, "после провалившейся записи мак обязан снова мочь спать")
        XCTAssertTrue(s.status.contains("устройство"),
                      "причина потери захвата — единственная подсказка, что чинить: \(s.status)")
        XCTAssertTrue(s.statusIsError)
        XCTAssertEqual(s.preservedFailure, s.status,
                       "без preservedFailure запоздавший статус демона затёр бы причину")
        XCTAssertNil(s.captureLossReason, "состояние не переживает встречу")
    }

    func testGiveUpOnNamedRefusalKeepsTheDaemonTextUntouched() {
        let s = SuflerService()
        let recipe = "корень данных не назван: передайте CHAROITE_ROOT=/путь"
        s.consumeForTest(#"{"type":"status","text":"\#(recipe)","error":true,"reason":"root_unnamed"}"#)
        XCTAssertEqual(s.status, recipe, "предпосылка: рецепт демона на экране")

        s.giveUp(.giveUpFatal("root_unnamed"))

        XCTAssertEqual(s.status, recipe, "текст демона несёт рецепт — наш «нажмите ещё раз» поверх вреден")
        XCTAssertTrue(s.statusIsError)
        XCTAssertEqual(s.preservedFailure, recipe)
    }
}
