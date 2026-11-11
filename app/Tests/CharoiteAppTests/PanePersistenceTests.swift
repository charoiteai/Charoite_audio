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

    func testЖиваяНитьНеПоказываетОтдельнуюКарточкуПодсказки() {
        for (hasHint, hinting) in [(true, false), (false, true), (true, true)] {
            let pane = SuflerView.paneStack(hasHint: hasHint, hinting: hinting,
                                            hasThread: true, running: true, liveFeed: true)
            XCTAssertFalse(pane.showHintCard)
            XCTAssertTrue(pane.showThread)
        }
    }

    @MainActor
    func testУстаревшийРучнойСтримИгнорируетсяПриАктивномНовомManualID() {
        let service = SuflerService()
        service.isHinting = true
        service.consumeForTest(#"{"type":"manual","state":"question","manual_id":"manual-2","text":"Новый вопрос?"}"#)
        service.consumeForTest(#"{"type":"hint","manual":true,"manual_id":"manual-1","text":"чужой хвост"}"#)
        service.consumeForTest(#"{"type":"hint_done","manual":true,"manual_id":"manual-1"}"#)
        XCTAssertTrue(service.isHinting)
        XCTAssertEqual(service.hint, "")
        service.consumeForTest(#"{"type":"hint","manual":true,"manual_id":"manual-2","text":"точный ответ"}"#)
        service.consumeForTest(#"{"type":"hint_done","manual":true,"manual_id":"manual-2"}"#)
        XCTAssertFalse(service.isHinting)
        XCTAssertEqual(service.hint, "точный ответ")
    }

    @MainActor
    func testПовторныйAskВРежимеЖивойНитиВытесняетПрежнийСтрим() throws {
        let service = SuflerService()
        let stdinPipe = Pipe(), stdoutPipe = Pipe()
        let launch = DaemonLaunch(process: Process(), stdinPipe: stdinPipe,
                                  stdout: stdoutPipe.fileHandleForReading, errHandle: nil)
        defer { launch.close() }
        try service.adoptLaunchForTest(launch, wire: false)
        service.consumeForTest(#"{"type":"feed_mode","enabled":true}"#)

        service.ask("Первый вопрос?")
        XCTAssertTrue(service.isHinting)
        service.consumeForTest(#"{"type":"manual","state":"question","manual_id":"manual-1","text":"Первый вопрос?"}"#)
        service.consumeForTest(#"{"type":"hint","manual":true,"manual_id":"manual-1","text":"черновик "}"#)

        // Второй ask отправляется, не дожидаясь завершения первого, и вытесняет manual-1
        service.ask("Второй вопрос?")
        XCTAssertTrue(service.isHinting)
        // Опоздавшие кадры manual-1 до и после регистрации manual-2 обязаны игнорироваться
        service.consumeForTest(#"{"type":"hint","manual":true,"manual_id":"manual-1","text":"хвост "}"#)
        service.consumeForTest(#"{"type":"hint_done","manual":true,"manual_id":"manual-1"}"#)
        XCTAssertTrue(service.isHinting)

        service.consumeForTest(#"{"type":"manual","state":"question","manual_id":"manual-2","text":"Второй вопрос?"}"#)
        service.consumeForTest(#"{"type":"hint","manual":true,"manual_id":"manual-2","text":"ответ 2"}"#)
        service.consumeForTest(#"{"type":"hint_done","manual":true,"manual_id":"manual-2"}"#)
        XCTAssertFalse(service.isHinting)
        XCTAssertEqual(service.hint, "ответ 2")

        try stdinPipe.fileHandleForWriting.close()
        let sent = String(data: stdinPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        XCTAssertTrue(sent.contains("ask Первый вопрос?\n"))
        XCTAssertTrue(sent.contains("ask Второй вопрос?\n"))
    }

    @MainActor
    func testОтклонённыйAskСбрасываетОжиданиеИУсекаетСверхдлинныйUTF8() throws {
        let clamped = SuflerService.utf8ClampedQuestion(String(repeating: "я", count: 4_500))
        XCTAssertEqual(clamped.utf8.count, 8_000)
        XCTAssertEqual(clamped.count, 4_000)

        let service = SuflerService()
        let stdinPipe = Pipe(), stdoutPipe = Pipe()
        let launch = DaemonLaunch(process: Process(), stdinPipe: stdinPipe,
                                  stdout: stdoutPipe.fileHandleForReading, errHandle: nil)
        defer { launch.close() }
        try service.adoptLaunchForTest(launch, wire: false)
        service.consumeForTest(#"{"type":"feed_mode","enabled":true}"#)

        // Одиночный отклонённый запрос без кадра manual(question) обязан снять isHinting
        service.ask("Первый вопрос?")
        XCTAssertTrue(service.isHinting)
        service.consumeForTest(#"{"type":"hint_done","manual":true,"manual_id":"manual-1","rejected":true}"#)
        XCTAssertFalse(service.isHinting)

        // Два быстрых ask подряд: отказ первого не сбивает FIFO-корреляцию второго
        service.ask("Второй вопрос?")
        service.ask("Третий вопрос?")
        XCTAssertTrue(service.isHinting)
        service.consumeForTest(#"{"type":"hint_done","manual":true,"manual_id":"manual-2","rejected":true}"#)
        XCTAssertTrue(service.isHinting)
        service.consumeForTest(#"{"type":"manual","state":"question","manual_id":"manual-3","text":"Третий вопрос?"}"#)
        service.consumeForTest(#"{"type":"hint","manual":true,"manual_id":"manual-3","text":"чистый ответ"}"#)
        service.consumeForTest(#"{"type":"hint_done","manual":true,"manual_id":"manual-3"}"#)
        XCTAssertFalse(service.isHinting)
        XCTAssertEqual(service.hint, "чистый ответ")
    }

    @MainActor
    func testСмертьИПерезапускДемонаСбрасываютКорреляциюРучныхВопросов() throws {
        let service = SuflerService()
        service.userStopped = true
        let stdin1 = Pipe(), stdout1 = Pipe()
        let launch1 = DaemonLaunch(process: Process(), stdinPipe: stdin1,
                                   stdout: stdout1.fileHandleForReading, errHandle: nil)
        defer { launch1.close() }
        try service.adoptLaunchForTest(launch1, wire: false)
        service.consumeForTest(#"{"type":"feed_mode","enabled":true}"#)

        service.ask("Вопрос до падения?")
        XCTAssertTrue(service.isHinting)
        launch1.receiveForTest(.eof)
        launch1.receiveForTest(.exit)
        XCTAssertFalse(service.isHinting)

        service.userStopped = false
        let stdin2 = Pipe(), stdout2 = Pipe()
        let launch2 = DaemonLaunch(process: Process(), stdinPipe: stdin2,
                                   stdout: stdout2.fileHandleForReading, errHandle: nil)
        defer { launch2.close() }
        try service.adoptLaunchForTest(launch2, wire: false)
        service.consumeForTest(#"{"type":"feed_mode","enabled":true}"#)

        service.ask("Вопрос новому демону?")
        service.consumeForTest(#"{"type":"manual","state":"question","manual_id":"manual-1","text":"Вопрос новому демону?"}"#)
        service.consumeForTest(#"{"type":"hint","manual":true,"manual_id":"manual-1","text":"свежий ответ"}"#)
        service.consumeForTest(#"{"type":"hint_done","manual":true,"manual_id":"manual-1"}"#)
        XCTAssertFalse(service.isHinting)
        XCTAssertEqual(service.hint, "свежий ответ")
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
        let s = SuflerService()   // свежий экземпляр: shared копит хвосты флашей (GLM r1)
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

    /// DS r1 по #473: планировщик коалессера обязан флашить САМ — тест с
    /// ручным flushLines() оставался бы зелёным при мёртвом Task.
    @MainActor func testCoalescerFlushesByItself() async {
        let s = SuflerService()
        s.consumeForTest(#"{"type":"transcript","speaker":"Т","plain":"сам","ts":"11:00"}"#)
        try? await Task.sleep(nanoseconds: 500_000_000)
        XCTAssertEqual(s.lines.last?.text.hasSuffix("сам"), true,
                       "коалессер не дофлашил за 500 мс — планировщик мёртв")
        s.consumeForTest(#"{"type":"transcript_markup","speaker":"Т","text":"— сам. — разметка"}"#)
        try? await Task.sleep(nanoseconds: 500_000_000)
        XCTAssertEqual(s.lines.last?.text, "— сам. — разметка", "markup не дошёл через тень")
    }
}
