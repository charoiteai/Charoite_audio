import XCTest
@testable import CharoiteApp

/// Смерть демона судится только после того, как его stdout дочитан (№619).
///
/// Раньше читатель трубы и обработчик выхода прыгали на главный актор
/// каждый своим `Task`, и при выходе раньше строки решение о повторе
/// принималось без причины отказа: рецепт `privacy_refused` превращался в
/// «восстанавливаю (1 из 3)». В проде это гонка (опыт 19.10: 5 из 200 при
/// одной уступке планировщика), поэтому здесь порядок задаёт тест — тот,
/// который гонка давала.
@MainActor
final class DaemonLaunchTests: XCTestCase {
    private let recipe = "llm.base_url вне белой грамматики: укажите адрес без зоны"
    private var refusalLine: String {
        #"{"type":"status","text":"\#(recipe)","error":true,"reason":"privacy_refused"}"#
    }

    /// Запуск без живого процесса: труба настоящая, чтобы `poll` потолка
    /// видел её состояние; запись в неё держит тест.
    private func idleLaunch() -> (DaemonLaunch, Pipe) {
        let out = Pipe()
        let l = DaemonLaunch(process: Process(), stdinPipe: Pipe(),
                             stdout: out.fileHandleForReading, errHandle: nil)
        return (l, out)
    }

    /// Таймеры под `taskpolicy -c background` сдвигаются: ждём условие, а
    /// не фиксированную паузу.
    private func spin(until cond: () -> Bool, timeout: TimeInterval = 5) {
        let deadline = Date().addingTimeInterval(timeout)
        while !cond(), Date() < deadline {
            RunLoop.main.run(until: Date().addingTimeInterval(0.01))
        }
    }

    private func assertGaveUpOnRecipe(_ s: SuflerService, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(s.status, recipe, "рецепт демона затёрт: \(s.status)", file: file, line: line)
        XCTAssertTrue(s.statusErrorFromDaemon, "fail() звался — значит, пошёл повтор", file: file, line: line)
        XCTAssertEqual(s.preservedFailure, recipe, "сдались не по причине демона", file: file, line: line)
    }

    func testExitBeforeTheRefusalLineStillGivesUp() throws {
        let s = SuflerService()
        let (l, _) = idleLaunch()
        try s.adoptLaunchForTest(l, wire: false)

        l.receiveForTest(.exit)
        XCTAssertTrue(s.process === l.process, "решение принято до EOF stdout — строка причины ещё в пути")

        l.receiveForTest(.output(Data((refusalLine + "\n").utf8)))
        l.receiveForTest(.eof)
        XCTAssertNil(s.process)
        assertGaveUpOnRecipe(s)
        XCTAssertTrue(l.closed, "концы запуска закрыты вместе с исходом")
    }

    func testRefusalWithoutTrailingNewlineIsReadAtEOF() throws {
        let s = SuflerService()
        let (l, _) = idleLaunch()
        try s.adoptLaunchForTest(l, wire: false)

        l.receiveForTest(.output(Data(refusalLine.utf8)))   // демон умер, не дописав \n
        l.receiveForTest(.eof)
        XCTAssertTrue(s.process === l.process, "EOF без выхода — процесс ещё жив")
        l.receiveForTest(.exit)
        assertGaveUpOnRecipe(s)
    }

    func testCeilingDecidesWhenAGrandchildHoldsStdout() throws {
        let s = SuflerService()
        s.userStopped = true       // решение «.none»: без перезапуска python из теста
        let (l, out) = idleLaunch()
        l.setCeilingForTest(0.05)
        try s.adoptLaunchForTest(l, wire: false)

        l.receiveForTest(.exit)   // write-конец открыт у теста, как у внука
        XCTAssertNotNil(s.process, "решение раньше потолка — EOF не ждали вовсе")
        spin(until: { s.process == nil })

        XCTAssertNil(s.process, "без EOF решение обязано прийти по потолку, иначе запись висит")
        XCTAssertTrue(l.closed)
        XCTAssertEqual(l.rearms, 0, "в трубе пусто — продлевать нечего")
        withExtendedLifetime(out) {}
    }

    func testCeilingWaitsWhileUnreadBytesSitInThePipe() throws {
        let s = SuflerService()
        s.userStopped = true
        let (l, out) = idleLaunch()
        l.setCeilingForTest(0.05)
        try s.adoptLaunchForTest(l, wire: false)
        out.fileHandleForWriting.write(Data("{}\n".utf8))   // читатель не успел их забрать

        l.receiveForTest(.exit)
        spin(until: { s.process == nil })

        XCTAssertEqual(l.rearms, DaemonLaunch.ceilingRearms, "потолок продлевается, пока байты лежат в трубе")
        XCTAssertNil(s.process, "продления конечны: читатель мёртв — решение всё равно приходит")
    }

    func testCeilingReadsTheLastLineWithoutNewline() throws {
        let s = SuflerService()
        let (l, out) = idleLaunch()
        l.setCeilingForTest(0.05)
        try s.adoptLaunchForTest(l, wire: false)

        l.receiveForTest(.output(Data(refusalLine.utf8)))   // без \n, и EOF не придёт: внук держит stdout
        l.receiveForTest(.exit)
        spin(until: { s.process == nil })

        XCTAssertEqual(s.status, recipe, "хвост строки не разобран на смерти по потолку: \(s.status)")
        XCTAssertTrue(s.statusErrorFromDaemon, "fail() звался — значит, пошёл повтор")
        withExtendedLifetime(out) {}
    }

    /// Закрытый запуск молчит: опоздавшие события старого не доходят до сервиса.
    func testClosedLaunchStaysSilentForTheSuccessor() throws {
        let s = SuflerService()
        let (old, _) = idleLaunch()
        try s.adoptLaunchForTest(old, wire: false)
        s.process = nil                 // так закрывает встречу остановка
        s.releaseLaunchAfterFinish()

        let (next, _) = idleLaunch()
        try s.adoptLaunchForTest(next, wire: false)
        let before = s.status

        old.receiveForTest(.output(Data((refusalLine + "\n").utf8)))
        old.receiveForTest(.exit)
        old.receiveForTest(.eof)

        XCTAssertEqual(s.status, before, "строка старого демона затёрла экран нового")
        XCTAssertTrue(s.launchForTest === next)
        XCTAssertTrue(s.process === next.process)
        XCTAssertFalse(next.closed, "исход старого запуска закрыл трубы преемника")
    }

    func testFinishDoesNotCloseALaunchWhoseDaemonIsStillTracked() throws {
        let s = SuflerService()
        let (l, _) = idleLaunch()
        try s.adoptLaunchForTest(l, wire: false)
        s.releaseLaunchAfterFinish()
        XCTAssertFalse(l.closed, "закрытие запуска мимо остановки глушит stdin живого демона")
        s.process = nil              // так делает finish остановки
        s.releaseLaunchAfterFinish()
        XCTAssertTrue(l.closed)
        XCTAssertNil(s.launchForTest)
    }

    func testNewLaunchClosesTheOpenOne() throws {
        let s = SuflerService()
        let (old, _) = idleLaunch()
        try s.adoptLaunchForTest(old, wire: false)
        let (next, _) = idleLaunch()
        try s.adoptLaunchForTest(next, wire: false)
        XCTAssertTrue(old.closed, "незакрытый прошлый запуск — утечка fd на каждом рестарте")
        XCTAssertFalse(next.closed)
    }

    /// Настоящий процесс через ту же проводку: строка отказа, затем выход 11.
    /// Читатель задержан на 5 мс — так прежняя проводка (`Task` на каждый
    /// колбэк) теряла причину всегда.
    func testRealProcessRefusalGivesUpThroughTheWiring() throws {
        for _ in 0..<20 {
            let s = SuflerService()
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/bin/sh")
            p.arguments = ["-c", "printf '%s\\n' \"$1\"; exit 11", "sh", refusalLine]
            let out = Pipe(), inp = Pipe()
            p.standardOutput = out
            p.standardInput = inp
            let l = DaemonLaunch(process: p, stdinPipe: inp, stdout: out.fileHandleForReading, errHandle: nil)
            l.readerDelayForTest = 5_000
            try s.adoptLaunchForTest(l, wire: true)

            spin(until: { s.process == nil })
            XCTAssertNil(s.process, "исход не пришёл за 5 с")
            // preservedFailure здесь не судим: закрытие встречи снимает его
            // асинхронно, пока крутится цикл ожидания.
            XCTAssertEqual(s.status, recipe, "рецепт демона затёрт: \(s.status)")
            XCTAssertTrue(s.statusErrorFromDaemon, "fail() звался — значит, пошёл повтор")
        }
    }
}
