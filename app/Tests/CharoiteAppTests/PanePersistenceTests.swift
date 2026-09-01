import XCTest
@testable import CharoiteApp

/// Правая панель суфлёра — стопка, не взаимоисключение (№22, регрессия #255
/// ловилась дважды: прежние тесты проверяли ВЫБОР текста, а панель целиком
/// прятала обёртка if isRunning, и все тесты оставались зелёными).
final class PanePersistenceTests: XCTestCase {
    func testНитьВиднаПослеОстановкиВстречи() {
        let pane = SuflerView.paneStack(hasHint: false, hinting: false,
                                        hasThread: true, running: false)
        XCTAssertTrue(pane.showThread)
        XCTAssertNil(pane.placeholder)
    }

    func testПодсказкаНеЗакрываетНить() {
        // прежний выбор «hint ИЛИ thread» позволял одной подсказке спрятать
        // нить до конца встречи — теперь оба видны одновременно
        let pane = SuflerView.paneStack(hasHint: true, hinting: false,
                                        hasThread: true, running: true)
        XCTAssertTrue(pane.showHintCard)
        XCTAssertTrue(pane.showThread)
    }

    func testИдущаяГенерацияПоказываетКарточкуБезТекста() {
        let pane = SuflerView.paneStack(hasHint: false, hinting: true,
                                        hasThread: true, running: true)
        XCTAssertTrue(pane.showHintCard)
    }

    func testПустаяПанельПослеСтопаВедётВПамять() {
        let pane = SuflerView.paneStack(hasHint: false, hinting: false,
                                        hasThread: false, running: false)
        XCTAssertNotNil(pane.placeholder)
        XCTAssertTrue(pane.placeholder?.contains("Память") == true
                      || pane.placeholder?.contains("Memory") == true
                      || pane.placeholder?.contains("记忆") == true)
    }

    func testПустаяПанельНаВстречеОбещаетНить() {
        let pane = SuflerView.paneStack(hasHint: false, hinting: false,
                                        hasThread: false, running: true)
        XCTAssertNotNil(pane.placeholder)
    }

    func testВыключенныеТумблерыНеОбещаютНить() {
        // Нить растёт только под toggles["hints"] демона: с выключенным
        // тумблером «появится через минуту» — обещание, которое не сбудется
        // никогда. Панель обязана назвать причину. Тезисный контур из панели
        // убран (пакет 24.08) — его тумблер в раскладке больше не участвует.
        let pane = SuflerView.paneStack(hasHint: false, hinting: false,
                                        hasThread: false, running: true,
                                        hintsOn: false)
        XCTAssertNotNil(pane.placeholder)
        XCTAssertFalse(pane.placeholder?.contains("минуту") == true,
                       "обещание нити при выключенных подсказках — враньё")
        // Уже накопленную нить выключенный тумблер прятать не смеет.
        let with = SuflerView.paneStack(hasHint: false, hinting: false,
                                        hasThread: true, running: true,
                                        hintsOn: false)
        XCTAssertTrue(with.showThread)
    }

    /// Политика гашения карточки нитью — вторая слепая зона №22 (ревью
    /// 16.08): стирание только что запрошенного ручного ответа и ампутация
    /// идущего авто-стрима жили в приватном consume и тестами не ловились.
    func testНитьГаситТолькоЗавершённыйАвтоконтент() {
        // бриф или отгоревшая авто-подсказка — гаснут (возраст по умолчанию ∞)
        XCTAssertTrue(SuflerService.threadClearsHint(
            isHinting: false, isAutoHinting: false, hintIsManual: false))
        // свежая авто-подсказка — живёт: за полминуты её не успевали
        // дочитать (просьба владельца 01.09)
        XCTAssertFalse(SuflerService.threadClearsHint(
            isHinting: false, isAutoHinting: false, hintIsManual: false,
            ageSeconds: 30))
        // отгоревшая своё — уступает нити
        XCTAssertTrue(SuflerService.threadClearsHint(
            isHinting: false, isAutoHinting: false, hintIsManual: false,
            ageSeconds: SuflerService.hintCardLifetime + 1))
        // ручной стрим в полёте — не трогать
        XCTAssertFalse(SuflerService.threadClearsHint(
            isHinting: true, isAutoHinting: false, hintIsManual: false))
        // авто-стрим в полёте — не резать пополам
        XCTAssertFalse(SuflerService.threadClearsHint(
            isHinting: false, isAutoHinting: true, hintIsManual: false))
        // завершённый ручной ответ — живёт до крестика или новой подсказки
        XCTAssertFalse(SuflerService.threadClearsHint(
            isHinting: false, isAutoHinting: false, hintIsManual: true))
    }

    /// Регрессия #255 оба раза приходила одним и тем же движением: панель
    /// целиком заворачивали в `if sufler.isRunning` — и юнит-тесты чистой
    /// функции оставались зелёными. Инвариант «панель не привязана к
    /// isRunning» юнитом не выражается, поэтому сторожим сам исходник:
    /// между объявлением rightPane и его заголовком не должно появиться
    /// ветвление по isRunning (внутри тела оно легитимно — облачная лента).
    func testПанельНеОбёрнутаВIsRunning() throws {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let source = try String(contentsOf: root.appendingPathComponent(
            "app/Sources/CharoiteApp/Views/Sufler/SuflerView.swift"), encoding: .utf8)
        let decl = try XCTUnwrap(source.range(of: "private var rightPane: some View {"))
        let head = source[decl.upperBound...]
        let title = try XCTUnwrap(head.range(of: "paneTitle("))
        // Шапка — от объявления до заголовка панели; комментарий там про
        // isRunning как раз говорит, поэтому ловим конструкцию, не слово.
        XCTAssertFalse(head[..<title.lowerBound].contains("if sufler.isRunning"),
                       "шапка rightPane снова гейтится по isRunning — регрессия #255, третий заход")
    }
}

@MainActor
final class LinesCoalescerTests: XCTestCase {
    /// №153: чанк транскрипта не публикует ленту немедленно — пачки уходят
    /// коалессером, ручной flushLines() делает тень видимой.
    func testShadowPublishesOnlyOnFlush() {
        let s = SuflerService.shared
        let before = s.lines
        s.consumeForTest(#"{"type":"transcript","speaker":"X","plain":"раз","ts":"10:00"}"#)
        XCTAssertEqual(s.lines, before, "публикация до flush — шторм вернулся")
        s.flushLines()
        XCTAssertEqual(s.lines.last?.text, "раз")
        s.consumeForTest(#"{"type":"rename","from":"X","to":"Ян"}"#)
        s.flushLines()
        XCTAssertEqual(s.lines.last?.speaker, "Ян")
        s.consumeForTest(#"{"type":"transcript","speaker":"Ян","plain":"два","ts":"10:00"}"#)
        s.flushLines()
        XCTAssertEqual(s.lines.last?.text, "раз два", "склейка одного голоса живёт в тени")
    }
}
