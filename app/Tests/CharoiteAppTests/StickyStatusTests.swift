import XCTest
@testable import CharoiteApp

/// №228: «собеседников в записи не будет» демон шлёт один раз на старте, а
/// следующий же статус («👥 живая диаризация включена», через секунду) стирал
/// его с экрана — единственный экранный носитель жил доли секунды. Липкое
/// предупреждение живёт до явного снятия или нового старта; обычные статусы
/// идут своим чередом рядом с ним.
@MainActor
final class StickyStatusTests: XCTestCase {
    private let warning = "⚠️ СОБЕСЕДНИКОВ В ЗАПИСИ НЕ БУДЕТ: системный звук не захвачен"

    func testStickyWarningSurvivesOrdinaryStatuses() {
        let s = SuflerService()
        s.consumeForTest(#"{"type":"status","text":"\#(warning)","error":true,"sticky":true}"#)
        XCTAssertEqual(s.stickyStatus, warning)
        XCTAssertTrue(s.statusIsError && s.statusErrorFromDaemon)

        s.consumeForTest(#"{"type":"status","text":"👥 живая диаризация голосов включена"}"#)
        XCTAssertEqual(s.status, "👥 живая диаризация голосов включена", "обычный статус идёт как раньше")
        XCTAssertFalse(s.statusIsError, "нелипкий флаг обычный статус снимает — это не менялось")
        XCTAssertEqual(s.stickyStatus, warning, "липкое обычным статусом не стирается")

        s.consumeForTest(#"{"type":"status","text":"⚡ отвечаю","error":false}"#)
        XCTAssertEqual(s.stickyStatus, warning, "error:false без ключа sticky — тоже не снятие")
    }

    func testStickyIsClearedOnlyExplicitly() {
        let s = SuflerService()
        s.consumeForTest(#"{"type":"status","text":"\#(warning)","error":true,"sticky":true}"#)
        s.consumeForTest(#"{"type":"status","text":"канал собеседников восстановлен","sticky":false}"#)
        XCTAssertNil(s.stickyStatus, "снятие — явное sticky:false")
        XCTAssertEqual(s.status, "канал собеседников восстановлен")
    }

    func testStickyWithoutErrorFlagIsStillSticky() {
        // контракт: липкость и окраска — независимые ключи; демон шлёт оба,
        // но липкость не должна зависеть от error
        let s = SuflerService()
        s.consumeForTest(#"{"type":"status","text":"\#(warning)","sticky":true}"#)
        XCTAssertEqual(s.stickyStatus, warning)
        XCTAssertFalse(s.statusIsError)
    }
}
