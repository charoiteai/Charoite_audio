import XCTest
@testable import CharoiteiOS

/// Конец звонка: сколько ждать микрофон и что делать, если не дождались.
///
/// 08.09, запись 30+ минут: посреди неё звонок на громкой связи. По `.ended`
/// рекордер делал три попытки по 0,6 с и на третьей неудаче звал `stop()`.
/// iOS после долгого звонка отдаёт вход не сразу — все три упёрлись в
/// занятый вход, файл закрылся на 20 секундах, а 30 минут встречи после
/// звонка не записались вовсе. Правила ожидания вынесены статикой, чтобы
/// их держал тест, а не следующая встреча.
final class RecorderAfterCallTests: XCTestCase {

    // MARK: - Сколько ждём

    func testДвеСекундыПослеЗвонкаЭтоЕщёНеПовод() {
        XCTAssertEqual(Recorder.actionAfterCall(waited: 1.8), .retry,
                       "1,8 с — столько ждал старый код, и этого не хватало")
        XCTAssertEqual(Recorder.actionAfterCall(waited: 30), .retry)
        XCTAssertEqual(Recorder.actionAfterCall(waited: 59), .retry)
    }

    func testЧерезМинутуЗакрываемФайлИПродолжаемНовым() {
        XCTAssertEqual(Recorder.actionAfterCall(waited: Recorder.resumeAfterCallBudget), .rotate,
                       "не дождались входа — встреча продолжается новым файлом, а не останавливается")
        XCTAssertEqual(Recorder.actionAfterCall(waited: 600), .rotate)
    }

    func testБюджетЧеловеческогоРазмера() {
        XCTAssertGreaterThanOrEqual(Recorder.resumeAfterCallBudget, 30,
                                    "короче — снова упрёмся в сворачивание сессии вызова")
        XCTAssertLessThanOrEqual(Recorder.resumeAfterCallBudget, 120,
                                 "дольше — минуты встречи молча уходят в никуда")
    }

    // MARK: - Как часто пробуем

    func testПаузыРастутАНеСтоятНаМесте() {
        XCTAssertEqual(Recorder.resumeAfterCallDelay(attempt: 1), 0.6,
                       "первые секунды — часто: обычно вход возвращается быстро")
        XCTAssertEqual(Recorder.resumeAfterCallDelay(attempt: 2), 0.6)
        XCTAssertEqual(Recorder.resumeAfterCallDelay(attempt: 3), 2)
        XCTAssertEqual(Recorder.resumeAfterCallDelay(attempt: 5), 2)
        XCTAssertEqual(Recorder.resumeAfterCallDelay(attempt: 6), 5)
        XCTAssertEqual(Recorder.resumeAfterCallDelay(attempt: 40), 5,
                       "дальше ровно: не спамим вход, но и не засыпаем")
    }

    func testПопытокХватаетЧтобыДойтиДоБюджета() {
        var waited: TimeInterval = 0
        var attempts = 0
        while Recorder.actionAfterCall(waited: waited) == .retry {
            attempts += 1
            waited += Recorder.resumeAfterCallDelay(attempt: attempts)
            XCTAssertLessThan(attempts, 100, "цикл обязан кончиться ротацией, а не крутиться вечно")
        }
        XCTAssertGreaterThan(attempts, 3, "старые три попытки — не ожидание")
        XCTAssertLessThan(attempts, 30, "и не сотни ударов по занятому входу")
    }

    // MARK: - Пока ждём — это всё ещё пауза

    /// Во время ожидания после звонка флаг прерывания остаётся: сторож
    /// застоя не должен считать свои попытки и ротировать поверх нашего
    /// цикла (тот же класс беды, что 07.08).
    func testОжиданиеПослеЗвонкаНеБудитСторожаЗастоя() {
        XCTAssertFalse(Recorder.shouldAutoResume(stalled: true, interrupted: true))
    }
}
