import Combine
import XCTest
@testable import CharoiteApp

/// Сверка n622p2-B-fix7 №1: запись встречи или выход начались, пока шла проверка
/// записи (`await` внутри CAS). Исход — как у отказа допуска до неё, а не
/// «не удалось записать config.yaml»; файл не тронут.
@MainActor
final class NemotronFix8Tests: XCTestCase {
    private var processes: [FakeNemotronProcess] = []
    private var recording = false
    private var root: URL!

    override func setUp() async throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("fix8-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root.appendingPathComponent("config"),
                                                withIntermediateDirectories: true)
        // Проверка записи отвечает «ок» через 0,6 с — окно, в котором меняется допуск.
        let sh = root.appendingPathComponent("slow_check.sh")
        try "#!/bin/sh\ncat >/dev/null\nsleep 0.6\necho ok\n".write(to: sh, atomically: true, encoding: .utf8)
        ConfigEditCheck.current = ConfigEditCheck(python: URL(fileURLWithPath: "/bin/sh"), script: sh.path)
        addTeardownBlock { @MainActor in ConfigEditCheck.current = .live }
    }

    override func tearDown() async throws {
        try? FileManager.default.removeItem(at: root)
    }

    private var configURL: URL { root.appendingPathComponent("config/config.yaml") }

    /// Проверка записи падает через 0,6 с с кодом 3 — сбой самой проверки, а не допуска.
    private func failingCheck() throws {
        let sh = root.appendingPathComponent("failing_check.sh")
        try "#!/bin/sh\ncat >/dev/null\nsleep 0.6\nexit 3\n".write(to: sh, atomically: true, encoding: .utf8)
        ConfigEditCheck.current = ConfigEditCheck(python: URL(fileURLWithPath: "/bin/sh"), script: sh.path)
    }

    private func makeService() -> NemotronEngineService {
        NemotronEngineService(
            makeProcess: { [unowned self] args, _, _, _ in
                let p = FakeNemotronProcess(arguments: args)
                self.processes.append(p)
                return p
            },
            currentRoot: { [unowned self] in self.root },
            isRecording: { [unowned self] in self.recording },
            refreshReadiness: {},
            lifecycleChanges: Empty().eraseToAnyPublisher(),
            voiceSetChanges: Empty().eraseToAnyPublisher())
    }

    private func eventually(_ what: String, _ cond: () -> Bool) async {
        for _ in 0..<300 {
            if cond() { return }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("не дождался: \(what)")
    }

    private func plan(state: String, _ key: String) -> LongProcessOutcome {
        NemotronPlanTests.outcome(NemotronPlanTests.json { d in
            var offer = d["offer"] as! [String: Any]
            offer["state"] = state
            offer[key] = ["allowed": true, "reason": ""]
            d["offer"] = offer
        })
    }

    func testFix8ЗаписьНачаласьВоВремяПроверкиЗаписи() async throws {
        let before = "sufler:\n  diarize_backend: sherpa\n"
        try before.write(to: configURL, atomically: true, encoding: .utf8)
        let s = makeService()
        s.startEnable()
        processes[0].finish(NemotronPlanTests.outcome("ok"))
        await eventually("план после пробы") { processes.count == 2 }
        processes[1].finish(plan(state: "ready_off", "enable"))
        try await Task.sleep(nanoseconds: 250_000_000)   // проверка записи идёт
        recording = true
        await eventually("исход") { if case .outcome = s.action { return true }; return false }
        guard case .outcome(let o) = s.action else { return XCTFail("нет исхода") }
        XCTAssertFalse(o.success)
        XCTAssertTrue(o.offerEnable, "после встречи — «Включить»: \(o.text)")
        XCTAssertFalse(o.text.contains("config.yaml"), o.text)
        XCTAssertEqual(try String(contentsOf: configURL, encoding: .utf8), before)
    }

    func testFix8ВыходВоВремяПроверкиВыключения() async throws {
        let before = "sufler:\n  diarize_backend: nemotron\n"
        try before.write(to: configURL, atomically: true, encoding: .utf8)
        let s = makeService()
        s.requestPlan()
        processes[0].finish(plan(state: "enabled", "disable"))
        await eventually("план") { if case .ready = s.plan { return true }; return false }
        let t = s.disable()
        XCTAssertNotNil(t)
        try await Task.sleep(nanoseconds: 250_000_000)   // проверка записи идёт
        s.terminationInProgress = true
        await t?.value
        guard case .outcome(let o) = s.action else { return XCTFail("нет исхода") }
        XCTAssertFalse(o.success)
        XCTAssertFalse(o.text.contains("config.yaml"), o.text)
        XCTAssertEqual(try String(contentsOf: configURL, encoding: .utf8), before)
    }

    // Сверка n622p2-B-fix8 №1: проверка упала сама, запись встречи началась во время
    // неё — причина сбоя видна, а не «идёт запись встречи».
    func testFix9СбойПроверкиПриЗаписиПоказываетПричину() async throws {
        try failingCheck()
        let before = "sufler:\n  diarize_backend: sherpa\n"
        try before.write(to: configURL, atomically: true, encoding: .utf8)
        let s = makeService()
        s.startEnable()
        processes[0].finish(NemotronPlanTests.outcome("ok"))
        await eventually("план после пробы") { processes.count == 2 }
        processes[1].finish(plan(state: "ready_off", "enable"))
        try await Task.sleep(nanoseconds: 250_000_000)   // проверка записи идёт
        recording = true
        await eventually("исход") { if case .outcome = s.action { return true }; return false }
        guard case .outcome(let o) = s.action else { return XCTFail("нет исхода") }
        XCTAssertFalse(o.success)
        XCTAssertTrue(o.text.hasSuffix(" 3"), "причина — код 3: \(o.text)")
        XCTAssertEqual(try String(contentsOf: configURL, encoding: .utf8), before)
    }

    // Сверка n622p2-B-fix8 №3, обратное направление: допуск свободен, проверка упала —
    // причина видна.
    func testFix9СбойПроверкиБезЗаписиПоказываетПричину() async throws {
        try failingCheck()
        let before = "sufler:\n  diarize_backend: sherpa\n"
        try before.write(to: configURL, atomically: true, encoding: .utf8)
        let s = makeService()
        s.startEnable()
        processes[0].finish(NemotronPlanTests.outcome("ok"))
        await eventually("план после пробы") { processes.count == 2 }
        processes[1].finish(plan(state: "ready_off", "enable"))
        await eventually("исход") { if case .outcome = s.action { return true }; return false }
        guard case .outcome(let o) = s.action else { return XCTFail("нет исхода") }
        XCTAssertFalse(o.success)
        XCTAssertTrue(o.text.hasSuffix(" 3"), "причина — код 3: \(o.text)")
        XCTAssertEqual(try String(contentsOf: configURL, encoding: .utf8), before)
    }

    // Сверка n622p2-B-fix8 №2: проверка выключения упала сама, выход начался во время
    // неё — причина видна, а не «приложение закрывается».
    func testFix9СбойПроверкиВыключенияПриВыходеПоказываетПричину() async throws {
        try failingCheck()
        let before = "sufler:\n  diarize_backend: nemotron\n"
        try before.write(to: configURL, atomically: true, encoding: .utf8)
        let s = makeService()
        s.requestPlan()
        processes[0].finish(plan(state: "enabled", "disable"))
        await eventually("план") { if case .ready = s.plan { return true }; return false }
        let t = s.disable()
        XCTAssertNotNil(t)
        try await Task.sleep(nanoseconds: 250_000_000)   // проверка записи идёт
        s.terminationInProgress = true
        await t?.value
        guard case .outcome(let o) = s.action else { return XCTFail("нет исхода") }
        XCTAssertFalse(o.success)
        XCTAssertTrue(o.text.hasSuffix(" 3"), "причина — код 3: \(o.text)")
        XCTAssertEqual(try String(contentsOf: configURL, encoding: .utf8), before)
    }

    // Сверка n622p2-B-fix9 (Important Haiku): допуск свободен, проверка «ок», recheck == nil,
    // а запись упала (каталог config/ без права записи) — причина записи видна, не «закрывается».
    func testFix10СбойЗаписиПослеПроверкиEnableПоказываетПричину() async throws {
        let before = "sufler:\n  diarize_backend: sherpa\n"
        try before.write(to: configURL, atomically: true, encoding: .utf8)
        let dir = configURL.deletingLastPathComponent()
        try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: dir.path)
        addTeardownBlock { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: dir.path) }
        let s = makeService()
        s.startEnable()
        processes[0].finish(NemotronPlanTests.outcome("ok"))
        await eventually("план после пробы") { processes.count == 2 }
        processes[1].finish(plan(state: "ready_off", "enable"))
        await eventually("исход") { if case .outcome = s.action { return true }; return false }
        guard case .outcome(let o) = s.action else { return XCTFail("нет исхода") }
        XCTAssertFalse(o.success)
        XCTAssertTrue(o.text.hasPrefix("не удалось записать config.yaml: "), "причина — запись: \(o.text)")
        XCTAssertFalse(o.text.contains("закрывается"), o.text)
        XCTAssertFalse(o.text.contains("запись встречи"), o.text)
        XCTAssertEqual(try String(contentsOf: configURL, encoding: .utf8), before)
    }

    func testFix10СбойЗаписиПослеПроверкиDisableПоказываетПричину() async throws {
        let before = "sufler:\n  diarize_backend: nemotron\n"
        try before.write(to: configURL, atomically: true, encoding: .utf8)
        let dir = configURL.deletingLastPathComponent()
        try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: dir.path)
        addTeardownBlock { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: dir.path) }
        let s = makeService()
        s.requestPlan()
        processes[0].finish(plan(state: "enabled", "disable"))
        await eventually("план") { if case .ready = s.plan { return true }; return false }
        let t = s.disable()
        XCTAssertNotNil(t)
        await t?.value
        guard case .outcome(let o) = s.action else { return XCTFail("нет исхода") }
        XCTAssertFalse(o.success)
        XCTAssertTrue(o.text.hasPrefix("не удалось записать config.yaml: "), "причина — запись: \(o.text)")
        XCTAssertFalse(o.text.contains("закрывается"), o.text)
        XCTAssertEqual(try String(contentsOf: configURL, encoding: .utf8), before)
    }
}
