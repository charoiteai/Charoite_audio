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
        // демон шлёт липкое БЕЗ error: «запись неполная» — не «запись сломалась»,
        // и флаг error не должен открывать окно, где критикал диска уступает
        // предупреждению (DS I2 по #538)
        s.consumeForTest(#"{"type":"status","text":"\#(warning)","error":false,"sticky":true}"#)
        XCTAssertEqual(s.stickyStatus, warning)
        XCTAssertFalse(s.statusIsError || s.statusErrorFromDaemon)

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
        // контракт: липкость и окраска — независимые ключи; липкость не
        // зависит от error
        let s = SuflerService()
        s.consumeForTest(#"{"type":"status","text":"\#(warning)","sticky":true}"#)
        XCTAssertEqual(s.stickyStatus, warning)
        XCTAssertFalse(s.statusIsError)
    }

    /// Приоритеты строки статуса — то, что чинили (DS r1 по #538): липкое
    /// переживает служебные строки, но уступает критикалу диска, ошибке
    /// демона и предупреждениям здоровья STT про «сейчас».
    func testLiveStatusPriorities() {
        func pick(critical: String? = nil, errorFromDaemon: Bool = false, isError: Bool = false,
                  health: String? = nil, sticky: String? = nil, status: String = "👥 диаризация") -> String {
            SuflerView.liveStatusText(stopConfirmPending: false, criticalHealthText: critical,
                                      errorFromDaemon: errorFromDaemon, isError: isError,
                                      healthText: health, sticky: sticky, status: status)
        }
        XCTAssertEqual(pick(sticky: warning), warning, "служебная строка липкое не прячет")
        XCTAssertEqual(pick(health: "⚠️ STT не отвечает 30 с", sticky: warning),
                       "⚠️ STT не отвечает 30 с", "здоровье STT — про сейчас, выше липкого")
        XCTAssertEqual(pick(critical: "⛔️ Аудио не пишется на диск", health: "⛔️ Аудио не пишется на диск",
                            sticky: warning), "⛔️ Аудио не пишется на диск", "критикал диска выше всего")
        XCTAssertEqual(pick(critical: "⛔️ диск", errorFromDaemon: true, isError: true, health: "⛔️ диск",
                            sticky: warning, status: "ЗАПИСЬ НА ДИСК ВЫКЛЮЧЕНА: нет места"),
                       "ЗАПИСЬ НА ДИСК ВЫКЛЮЧЕНА: нет места", "причина от демона важнее генерик-баннера")
        XCTAssertEqual(pick(isError: true, sticky: warning, status: "⛔️ Захват звука потерян"),
                       "⛔️ Захват звука потерян", "свежая ошибка выше липкого")
        XCTAssertEqual(pick(), "👥 диаризация")
    }
}
