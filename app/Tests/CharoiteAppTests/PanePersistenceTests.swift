import XCTest
@testable import CharoiteApp

/// Панель подсказки после «Стоп» (жалоба 07.08).
///
/// Раньше содержимое панели было привязано к тому, идёт ли запись: остановка
/// встречи мгновенно подменяла нить приглашением спросить про архив. Текст при
/// этом никуда не девался — его просто переставали показывать. Именно в первую
/// минуту после встречи итог дочитывают и копируют, поэтому правило проверяется
/// тестом, а не следующей встречей.
final class PanePersistenceTests: XCTestCase {

    func testНитьОстаётсяПослеОстановкиВстречи() {
        XCTAssertEqual(
            SuflerView.paneSource(running: false, hasHint: false,
                                  hasThread: true, hasArchive: false),
            .thread,
            "после «Стоп» нить обязана остаться на экране")
    }

    func testПодсказкаОстаётсяПослеОстановкиВстречи() {
        XCTAssertEqual(
            SuflerView.paneSource(running: false, hasHint: true,
                                  hasThread: true, hasArchive: false),
            .hint,
            "свежая подсказка важнее нити и после остановки")
    }

    func testВопросПоАрхивуЗамещаетИтогВстречи() {
        XCTAssertEqual(
            SuflerView.paneSource(running: false, hasHint: true,
                                  hasThread: true, hasArchive: true),
            .archive,
            "спросили про архив — ответ главнее прошлой встречи")
    }

    func testВоВремяВстречиАрхивНеПеребиваетНить() {
        XCTAssertEqual(
            SuflerView.paneSource(running: true, hasHint: false,
                                  hasThread: true, hasArchive: true),
            .thread,
            "идёт встреча — на экране разговор, а не архивная выдача")
    }

    func testПустоЕстьПусто() {
        XCTAssertEqual(
            SuflerView.paneSource(running: true, hasHint: false,
                                  hasThread: false, hasArchive: false),
            .placeholder)
        XCTAssertEqual(
            SuflerView.paneSource(running: false, hasHint: false,
                                  hasThread: false, hasArchive: false),
            .placeholder)
    }
}
