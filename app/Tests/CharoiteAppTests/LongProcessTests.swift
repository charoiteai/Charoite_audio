import Darwin
import XCTest
@testable import CharoiteApp

/// Помощник долгого процесса установщика Nemotron на поддельном установщике
/// (`/bin/sh -c`): строки по ходу, отмена по группе, потолок EOF, исход целиком.
@MainActor
final class LongProcessTests: XCTestCase {
    private var root: URL!

    override func setUp() async throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("longprocess-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    override func tearDown() async throws {
        try? FileManager.default.removeItem(at: root)
    }

    /// Журнал вызовов подменённой функции сигнала.
    final class SignalLog: @unchecked Sendable {
        private let lock = NSLock()
        private var calls: [(pid_t, Int32)] = []
        func add(_ target: pid_t, _ sig: Int32) {
            lock.lock(); calls.append((target, sig)); lock.unlock()
        }
        var all: [(pid_t, Int32)] {
            lock.lock(); defer { lock.unlock() }
            return calls
        }
        func clear() { lock.lock(); calls.removeAll(); lock.unlock() }
    }

    /// Строки stdout с моментом прихода.
    final class Lines {
        var items: [(String, Date)] = []
        var waiters: [(Int, XCTestExpectation)] = []
        func add(_ line: String) {
            items.append((line, Date()))
            for (n, e) in waiters where items.count == n { e.fulfill() }
        }
        func expect(_ n: Int, _ test: XCTestCase) -> XCTestExpectation {
            let e = test.expectation(description: "\(n) строк")
            if items.count >= n { e.fulfill() } else { waiters.append((n, e)) }
            return e
        }
    }

    private func make(_ script: String,
                      eofCeiling: TimeInterval = 2,
                      signal: @escaping LongProcess.SignalFunction = LongProcess.defaultSignal,
                      lines: Lines? = nil) -> LongProcess {
        LongProcess(arguments: ["-c", script], root: root,
                    executable: URL(fileURLWithPath: "/bin/sh"),
                    eofCeiling: eofCeiling, signal: signal,
                    onLine: lines.map { l in { l.add($0) } })
    }

    private func dead(_ pid: pid_t, within seconds: TimeInterval = 3) async -> Bool {
        let until = Date().addingTimeInterval(seconds)
        while Date() < until {
            if kill(pid, 0) == -1 && errno == ESRCH { return true }
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
        return false
    }

    // а) строки stdout приходят до выхода процесса
    func testСтрокиПриходятПоХоду() async {
        let lines = Lines()
        let p = make("echo first; sleep 1; echo second", lines: lines)
        let outcome = await p.run()
        let end = Date()
        await fulfillment(of: [lines.expect(2, self)], timeout: 2)
        XCTAssertEqual(outcome.code, 0)
        XCTAssertEqual(lines.items.map(\.0), ["first", "second"])
        XCTAssertLessThan(lines.items[0].1, end.addingTimeInterval(-0.5),
                          "первая строка обязана прийти, пока процесс жив")
    }

    // б) отмена гасит и ребёнка в той же группе
    func testОтменаГаситРебёнкаГруппы() async {
        let lines = Lines()
        let p = make("sleep 30 & echo $!; wait", lines: lines)
        async let outcome = p.run()
        await fulfillment(of: [lines.expect(1, self)], timeout: 5)
        let child = pid_t(lines.items[0].0)!
        p.cancel(grace: 5)
        let result = await outcome
        XCTAssertTrue(result.cancelRequested)
        let gone = await dead(child)
        XCTAssertTrue(gone, "фоновый ребёнок пережил отмену — бьём по pid, а не по группе")
        if !gone { kill(child, SIGKILL) }
    }

    // в) SIGTERM проигнорирован — SIGKILL по сроку
    func testИгнорSIGTERMДобиваетсяSIGKILL() async {
        let lines = Lines()
        let p = make("trap '' TERM; echo ready; while :; do sleep 0.1; done", lines: lines)
        async let outcome = p.run()
        await fulfillment(of: [lines.expect(1, self)], timeout: 5)
        let started = Date()
        p.cancel(grace: 0.5)
        let result = await outcome
        XCTAssertTrue(result.cancelRequested)
        XCTAssertEqual(result.reason, .uncaughtSignal)
        XCTAssertEqual(result.code, SIGKILL)
        XCTAssertLessThan(Date().timeIntervalSince(started), 4)
    }

    // г) окружение ребёнка: группа и корень данных
    func testОкружениеРебёнка() async {
        let p = make("echo \"$CHAROITE_INSTALL_NEW_PGROUP|$CHAROITE_ROOT\"")
        let result = await p.run()
        XCTAssertEqual(result.stdout.trimmingCharacters(in: .whitespacesAndNewlines),
                       "1|\(root.path)")
    }

    // д) Foundation Process запускает ребёнка лидером своей группы
    func testРебёнокЛидерСвоейГруппы() async {
        let p = make("echo \"$$ $(ps -o pgid= -p $$)\"")
        let result = await p.run()
        let parts = result.stdout.split(whereSeparator: { $0 == " " || $0 == "\n" })
        XCTAssertEqual(parts.count, 2, result.stdout)
        XCTAssertEqual(parts.first, parts.last,
                       "ребёнок не лидер группы — kill(-pid) не достанет pip и пробу")
    }

    // е) 200 КБ в каждый канал не вешают процесс
    func testБольшойВыводНеВешает() async throws {
        let p = make("head -c 204800 /dev/zero | tr '\\0' a; "
                     + "head -c 204800 /dev/zero | tr '\\0' b >&2; echo >&2; echo конец >&2")
        let done = expectation(description: "процесс вышел")
        var outcome: LongProcessOutcome?
        Task { outcome = await p.run(); done.fulfill() }
        await fulfillment(of: [done], timeout: 10)
        let result = try XCTUnwrap(outcome)
        XCTAssertEqual(result.code, 0)
        XCTAssertGreaterThanOrEqual(result.stdout.utf8.count, 204_800)
        XCTAssertEqual(result.stderrTail, "конец")
    }

    // ж) код 13 и строка stderr; строка «отмена пришла после замены»
    func testКодИПоследняяСтрокаStderr() async {
        let cancelled = await make("echo 'шум' >&2; echo 'отменено: получен сигнал 15' >&2; exit 13").run()
        XCTAssertEqual(cancelled.code, 13)
        XCTAssertEqual(cancelled.stderrTail, "отменено: получен сигнал 15")
        XCTAssertFalse(cancelled.cleanupAfterCommit)

        let late = await make("echo 'отмена пришла после замены — движок уже поставлен, доделываю уборку' >&2; "
                              + "echo 'готово: движок nemotron'; exit 0").run()
        XCTAssertEqual(late.code, 0)
        XCTAssertTrue(late.cleanupAfterCommit)
    }

    /// Подделка сигнала: доставляет по-настоящему, но о группе всегда говорит
    /// ESRCH — так видно, идёт ли запасной сигнал по pid.
    private func esrchForGroups(_ log: SignalLog) -> LongProcess.SignalFunction {
        { target, sig in
            log.add(target, sig)
            let r = kill(target, sig) == 0 ? 0 : errno
            return target < 0 ? ESRCH : r
        }
    }

    // з) окно «лидер вышел, исход не опубликован»
    func testОтменаПослеВыходаЛидераНеБьётПоPid() async {
        let log = SignalLog()
        let lines = Lines()
        let p = make("sleep 8 & echo $!; exit 0", eofCeiling: 1.5,
                     signal: esrchForGroups(log), lines: lines)
        async let outcome = p.run()
        await fulfillment(of: [lines.expect(1, self)], timeout: 5)
        let holder = pid_t(lines.items[0].0)!
        try? await Task.sleep(nanoseconds: 400_000_000)   // лидер вышел, канал держит sleep
        let pid = p.processID
        p.cancel(grace: 5)
        let result = await outcome
        XCTAssertFalse(log.all.contains { $0.0 > 0 }, "запасной kill(pid) после выхода лидера: \(log.all)")
        XCTAssertTrue(log.all.contains { $0.0 == -pid && $0.1 == SIGTERM })
        XCTAssertFalse(result.cancelRequested, "код 0 после выхода лидера — поставлено, а не отменено")
        XCTAssertEqual(result.code, 0)
        _ = await dead(holder)

        log.clear()
        p.cancel(grace: 0.1)
        XCTAssertTrue(log.all.isEmpty, "после публикации отмена ничего не шлёт")
    }

    func testПотолокEOFДобиваетГруппуБезОтмены() async {
        let log = SignalLog()
        let p = make("sleep 8 & echo $!; exit 0", eofCeiling: 0.5,
                     signal: { t, s in log.add(t, s); return kill(t, s) == 0 ? 0 : errno })
        let result = await p.run()
        let pid = p.processID
        XCTAssertEqual(log.all.map { "\($0.0) \($0.1)" }, ["\(-pid) \(SIGKILL)"])
        XCTAssertFalse(result.cancelRequested)
    }

    func testЗапасной_killPidДоВыходаЛидера() async {
        let log = SignalLog()
        let lines = Lines()
        let p = make("echo ready; exec sleep 30", signal: esrchForGroups(log), lines: lines)
        async let outcome = p.run()
        await fulfillment(of: [lines.expect(1, self)], timeout: 5)
        let pid = p.processID
        p.cancel(grace: 5)
        let result = await outcome
        XCTAssertTrue(result.cancelRequested)
        XCTAssertTrue(log.all.contains { $0.0 == pid && $0.1 == SIGTERM },
                      "на ESRCH от группы при живом лидере обязан идти запасной kill(pid): \(log.all)")
    }

    // и) короткий процесс: stdout исхода всегда целиком
    func testStdoutЦеликомНаКороткомПроцессе() async {
        for i in 0..<200 {
            let result = await make("head -c 1024 /dev/zero | tr '\\0' x; exit 0").run()
            XCTAssertEqual(result.stdout.utf8.count, 1024, "прогон \(i)")
            if result.stdout.utf8.count != 1024 { break }
        }
    }

    // к) держатель канала не вешает исход и не остаётся сиротой
    func testДержательКаналаДобиваетсяПоПотолку() async {
        let lines = Lines()
        let p = make("sleep 8 & echo $!; exit 0", lines: lines)
        let result = await p.run()
        let end = Date()
        await fulfillment(of: [lines.expect(1, self)], timeout: 2)
        let holder = pid_t(lines.items[0].0)!
        XCTAssertEqual(result.code, 0)
        XCTAssertLessThan(end.timeIntervalSince(lines.items[0].1), 2.5,
                          "исход ждал держателя канала дольше потолка")
        let gone = await dead(holder, within: 1)
        XCTAssertTrue(gone, "держатель канала пережил исход")
        if !gone { kill(holder, SIGKILL) }
    }

    // л) отмена до запуска и отказ функции сигнала при pid ≤ 1
    func testОтменаДоЗапуска() async {
        let log = SignalLog()
        let marker = root.appendingPathComponent("started")
        let p = make("touch '\(marker.path)'", signal: { t, s in log.add(t, s); return 0 })
        p.cancel(grace: 0.5)
        let result = await p.run()
        XCTAssertTrue(result.notStarted)
        XCTAssertTrue(result.cancelRequested)
        XCTAssertFalse(FileManager.default.fileExists(atPath: marker.path), "процесс запустился после отмены")
        XCTAssertTrue(log.all.isEmpty)
    }

    func testФункцияСигналаОтказываетПриМаломPid() {
        let log = SignalLog()
        let p = make("true", signal: { t, s in log.add(t, s); return 0 })
        for target: pid_t in [0, 1, -1] {
            XCTAssertEqual(p.send(target, SIGTERM), EINVAL)
        }
        XCTAssertTrue(log.all.isEmpty, "kill(0) бьёт по группе приложения: \(log.all)")
    }

    // м) после EOF обработчик канала снят
    func testОбработчикСнятПослеEOF() async {
        let p = make("exec 1>&-; sleep 1; echo конец >&2")
        let result = await p.run()
        XCTAssertEqual(result.stderrTail, "конец")
        XCTAssertLessThanOrEqual(p.handlerCallsAfterEOF, 1)
    }

    // run() бросил — исход с текстом ошибки, без падения
    func testНетИнтерпретатора() async {
        let p = LongProcess(arguments: [], root: root,
                            executable: root.appendingPathComponent("нет-такого"))
        let result = await p.run()
        XCTAssertTrue(result.notStarted)
        XCTAssertFalse(result.cancelRequested)
        XCTAssertFalse(result.stderrTail.isEmpty)
    }
}
