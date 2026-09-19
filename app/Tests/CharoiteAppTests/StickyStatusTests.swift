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

    /// №310: липкость — поле от писателя, не маркер в тексте. Текст без единого
    /// маркера с `sticky: true` — липкий (Minor DS входного круга).
    func testAnyTextWithStickyFlagIsSticky() {
        let s = SuflerService()
        s.consumeForTest(#"{"type":"status","text":"любой текст без маркеров","sticky":true,"topic":"channel_loss"}"#)
        XCTAssertEqual(s.stickyStatus, "любой текст без маркеров")
        s.consumeForTest(#"{"type":"status","text":"⚠️ СОБЕСЕДНИКОВ В ЗАПИСИ НЕ БУДЕТ как подстрока без ключа"}"#)
        XCTAssertEqual(s.stickyStatus, "любой текст без маркеров", "маркер в тексте без ключа слой не трогает")
    }

    /// №310: у слоёв есть владелец. Отбой демона (`sticky: false`) снимает
    /// только свою тему; слой захвата («права на микрофон нет») переживает его.
    func testDaemonClearDoesNotDropTheCaptureLayer() {
        let s = SuflerService()
        s.setSticky(SuflerService.StickyTopic.capture, "Права на микрофон нет — голос владельца не запишется")
        s.consumeForTest(#"{"type":"status","text":"\#(warning)","sticky":true,"topic":"channel_loss"}"#)
        XCTAssertEqual(s.stickyLayers.count, 2)
        XCTAssertEqual(s.stickyStatus, warning + " · Права на микрофон нет — голос владельца не запишется",
                       "оба факта на экране, потеря канала первой")
        s.consumeForTest(#"{"type":"status","text":"✅ канал снова пишется","sticky":false,"topic":"channel_loss"}"#)
        XCTAssertEqual(s.stickyStatus, "Права на микрофон нет — голос владельца не запишется",
                       "отбой демона снял свой слой, слой захвата остался")
        s.consumeForTest(#"{"type":"status","text":"⚠️ снова потеря","sticky":true}"#)
        XCTAssertEqual(s.stickyLayers[SuflerService.StickyTopic.channelLoss], "⚠️ снова потеря",
                       "демон без topic — тема потери канала (совместимость с 0.82)")
    }

    /// Слой уведомлений уступает слою демона по приоритету и возвращается на
    /// экран после его снятия; старт чистит все слои.
    func testNotificationsLayerYieldsAndReturns() {
        let s = SuflerService()
        s.noteNotificationsDenied()
        XCTAssertEqual(s.stickyLayers.count, 1)
        let notice = s.stickyInfo
        XCTAssertNotNil(notice)
        XCTAssertNil(s.stickyStatus, "справка — не проблема: живую строку не прячет и не красит (Important DS круга по №310)")
        s.consumeForTest(#"{"type":"status","text":"\#(warning)","sticky":true,"topic":"channel_loss"}"#)
        XCTAssertEqual(s.stickyStatus, warning, "потеря канала — слой-проблема, подсказка в него не подмешивается")
        XCTAssertEqual(s.stickyInfo, notice)
        s.consumeForTest(#"{"type":"status","text":"ожил","sticky":false,"topic":"channel_loss"}"#)
        XCTAssertNil(s.stickyStatus, "проблем не осталось")
        XCTAssertEqual(s.stickyInfo, notice, "после отбоя подсказка на месте, а не потеряна навсегда")
        s.noteNotificationsDenied()
        XCTAssertEqual(s.stickyLayers.count, 1, "один раз за встречу — флаг, не гонка слотов")
        XCTAssertLessThan(SuflerService.stickyRank[SuflerService.StickyTopic.channelLoss]!,
                          SuflerService.stickyRank[SuflerService.StickyTopic.capture]!)
        XCTAssertLessThan(SuflerService.stickyRank[SuflerService.StickyTopic.capture]!,
                          SuflerService.stickyRank[SuflerService.StickyTopic.notifications]!)
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
        // слой-справка едет хвостом за живой строкой, не вместо неё; при пустой строке — сам
        XCTAssertEqual(SuflerView.liveStatusText(stopConfirmPending: false, criticalHealthText: nil,
                                                 errorFromDaemon: false, isError: false, healthText: nil,
                                                 sticky: nil, status: "⚡ отвечаю", info: "Уведомления выключены"),
                       "⚡ отвечаю · Уведомления выключены")
        XCTAssertEqual(SuflerView.liveStatusText(stopConfirmPending: false, criticalHealthText: nil,
                                                 errorFromDaemon: false, isError: false, healthText: nil,
                                                 sticky: warning, status: "⚡ отвечаю", info: "Уведомления выключены"),
                       warning, "слой-проблема выше и живой строки, и справки")
    }
}
