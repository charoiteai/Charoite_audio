import XCTest
@testable import CharoiteApp

/// «Идёт запись» без времени — обещание без доказательства.
///
/// Пульсирующая волна в шапке говорит «работает», но не отвечает на вопрос,
/// который человек задаёт, вернувшись к ноутбуку: сколько уже пишется и не
/// зависло ли всё полчаса назад. Часы отвечают — но только если читаются
/// боковым зрением и не врут.
final class RecordingClockTests: XCTestCase {
    func testMinutesKeepLeadingZeroSoTheLineDoesNotJump() {
        // мм:сс с ведущим нулём у секунд: строка не должна менять ширину
        // каждую секунду, иначе таймер дёргает соседние элементы шапки
        XCTAssertEqual(SuflerService.clockText(0), "0:00")
        XCTAssertEqual(SuflerService.clockText(7), "0:07")
        XCTAssertEqual(SuflerService.clockText(65), "1:05")
        XCTAssertEqual(SuflerService.clockText(18 * 60 + 42), "18:42")
    }

    func testHoursAppearOnlyAfterAnHour() {
        // час — редкость, но встречи бывают длинные; до часа лишний «0:»
        // только мешает читать
        XCTAssertEqual(SuflerService.clockText(59 * 60 + 59), "59:59")
        XCTAssertEqual(SuflerService.clockText(3600), "1:00:00")
        XCTAssertEqual(SuflerService.clockText(3600 + 18 * 60 + 42), "1:18:42")
    }

    func testNegativeAndFractionalSecondsDoNotProduceGarbage() {
        // время приходит из Date().timeIntervalSince — при переводе часов
        // назад оно бывает отрицательным, и «-1:-1» на экране недопустимо
        XCTAssertEqual(SuflerService.clockText(-5), "0:00")
        XCTAssertEqual(SuflerService.clockText(9.99), "0:09")
    }

    func testVeryShortRecordingIsWorthConfirming() {
        // Промах по кнопке и настоящая короткая встреча выглядят одинаково,
        // поэтому граница нужна явная и одна на всё приложение.
        XCTAssertGreaterThan(SuflerService.tooShortToStop, 0)
        XCTAssertLessThan(SuflerService.tooShortToStop, 60,
                          "подтверждение на минутной записи будет мешать")
    }
}
