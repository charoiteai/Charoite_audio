import XCTest
@testable import CharoiteiOS

/// Аудит 13.09 (DS I4 / GLM I3): ошибка кодека посреди встречи останавливала
/// запись наглухо — остаток встречи не писался. Теперь файл закрывается и
/// встреча продолжается новым, а серия ошибок подряд — честный стоп.
final class RecorderEncodeErrorTests: XCTestCase {
    func testFirstErrorsRotateTheFile() {
        XCTAssertEqual(Recorder.actionAfterEncodeError(consecutive: 1), .rotate)
        XCTAssertEqual(Recorder.actionAfterEncodeError(consecutive: Recorder.maxEncodeErrors - 1), .rotate)
    }

    func testSeriesOfErrorsStopsInsteadOfSpawningEmptyChunks() {
        XCTAssertEqual(Recorder.actionAfterEncodeError(consecutive: Recorder.maxEncodeErrors), .stop)
    }
}
