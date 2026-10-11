import Combine
import XCTest
@testable import CharoiteApp

/// Подделка процесса кнопки: исход отдаёт тест.
final class FakeNemotronProcess: NemotronProcess, @unchecked Sendable {
    let arguments: [String]
    private let lock = NSLock()
    private var continuation: CheckedContinuation<LongProcessOutcome, Never>?
    private var pendingOutcome: LongProcessOutcome?
    private(set) var cancels: [TimeInterval] = []

    init(arguments: [String]) { self.arguments = arguments }

    func run() async -> LongProcessOutcome {
        await withCheckedContinuation { c in
            lock.lock()
            if let o = pendingOutcome { lock.unlock(); c.resume(returning: o); return }
            continuation = c
            lock.unlock()
        }
    }

    func finish(_ o: LongProcessOutcome) {
        lock.lock()
        if let c = continuation { continuation = nil; lock.unlock(); c.resume(returning: o); return }
        pendingOutcome = o
        lock.unlock()
    }

    func cancel(grace: TimeInterval) {
        lock.lock(); cancels.append(grace); lock.unlock()
    }

    var isPlan: Bool { arguments.contains("--plan") }
}

/// Служба кнопки Nemotron: занятость по методам (тест 6), single-flight плана
/// и две переменные (тест 10) — №622.
@MainActor
final class NemotronEngineServiceTests: XCTestCase {
    private var processes: [FakeNemotronProcess] = []
    private var recording = false
    private var voiceSetBusy = false
    private var lifecycle = PassthroughSubject<Void, Never>()
    private var root: URL!

    override func setUp() async throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("nemo-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root.appendingPathComponent("config"), withIntermediateDirectories: true)
        try "sufler:\n  diarize_backend: sherpa\n".write(to: configURL, atomically: true, encoding: .utf8)
    }

    override func tearDown() async throws {
        try? FileManager.default.removeItem(at: root)
    }

    private var configURL: URL { root.appendingPathComponent("config/config.yaml") }

    private func makeService() -> NemotronEngineService {
        NemotronEngineService(
            makeProcess: { [unowned self] args, _, _, _ in
                let p = FakeNemotronProcess(arguments: args)
                self.processes.append(p)
                return p
            },
            currentRoot: { [unowned self] in self.root },
            isRecording: { [unowned self] in self.recording },
            voiceSetInstalling: { [unowned self] in self.voiceSetBusy },
            refreshReadiness: {},
            lifecycleChanges: lifecycle.eraseToAnyPublisher(),
            voiceSetChanges: Empty().eraseToAnyPublisher())
    }

    private func eventually(_ what: String, _ cond: () -> Bool) async {
        for _ in 0..<300 {
            if cond() { return }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("не дождался: \(what)")
    }

    private func planOutcome(_ plan: (inout [String: Any]) -> Void = { _ in }) -> LongProcessOutcome {
        NemotronPlanTests.outcome(NemotronPlanTests.json(plan))
    }

    private func planText(state: String, enable: Bool) -> (inout [String: Any]) -> Void {
        { d in
            var offer = d["offer"] as! [String: Any]
            offer["state"] = state
            offer["enable"] = ["allowed": enable, "reason": "нельзя"]
            d["offer"] = offer
        }
    }

    // MARK: 6. занятость, запись, выход

    func testStartПриЧужойУстановкеНичегоНеЗапускает() {
        let s = makeService()
        voiceSetBusy = true
        s.startInstall(); s.startEnable()
        XCTAssertTrue(processes.isEmpty)
        XCTAssertEqual(s.action, .idle)
    }

    /// Боевой признак чужой установки — `progress[diarizationKey]` набора голосов.
    func testStartПриЗагрузкеНабораГолосовНичегоНеЗапускает() {
        let s = NemotronEngineService(
            makeProcess: { [unowned self] args, _, _, _ in
                let p = FakeNemotronProcess(arguments: args); self.processes.append(p); return p
            },
            currentRoot: { [unowned self] in self.root },
            isRecording: { false },
            refreshReadiness: {},
            lifecycleChanges: Empty().eraseToAnyPublisher(),
            voiceSetChanges: Empty().eraseToAnyPublisher())
        ModelPullService.shared.progress[ModelPullService.diarizationKey] = "качаю…"
        defer { ModelPullService.shared.progress[ModelPullService.diarizationKey] = nil }
        s.startInstall(); s.startEnable()
        XCTAssertTrue(processes.isEmpty)
    }

    func testStartВоВремяЗаписиНичегоНеЗапускает() {
        let s = makeService()
        recording = true
        s.startInstall(); s.startEnable()
        XCTAssertTrue(processes.isEmpty)
    }

    func testStartПриВыходеНичегоНеЗапускает() {
        let s = makeService()
        s.terminationInProgress = true
        s.startInstall(); s.startEnable()
        XCTAssertTrue(processes.isEmpty)
    }

    func testStartВоВремяСвоегоДействияНичегоНеЗапускает() {
        let s = makeService()
        s.startInstall()
        s.startEnable()
        XCTAssertEqual(processes.count, 1)
    }

    /// Сквозной путь: установка → код 0 → новый план → условная запись → план для показа.
    func testУстановкаПишетКлючПослеСвежегоПлана() async throws {
        let s = makeService()
        s.startInstall()
        XCTAssertEqual(processes.first?.arguments, ["nemotron"])
        XCTAssertEqual(s.action, .running(.install, lastLine: ""))
        processes[0].finish(NemotronPlanTests.outcome("готово"))
        await eventually("план после установки") { processes.count == 2 }
        XCTAssertTrue(processes[1].isPlan)
        XCTAssertTrue(s.isActive, "пока решение не принято, действие идёт")
        processes[1].finish(planOutcome(planText(state: "ready_off", enable: true)))
        await eventually("исход") { if case .outcome = s.action { return true }; return false }
        XCTAssertEqual(s.action, .outcome(.init(text: "Nemotron поставлен и включён", success: true)))
        XCTAssertEqual(AppSettings.configValue("diarize_backend", root: root), "nemotron")
        await eventually("план для показа") { processes.count == 3 }
    }

    func testВключитьИдётЧерезCheck() async {
        let s = makeService()
        s.startEnable()
        XCTAssertEqual(processes.first?.arguments, ["nemotron", "--check"])
        processes[0].finish(NemotronPlanTests.outcome("", code: 1, stderr: "проверка не выполнена: лок"))
        await eventually("исход") { if case .outcome = s.action { return true }; return false }
        guard case .outcome(let o) = s.action else { return XCTFail() }
        XCTAssertEqual(o.text, "проверка не выполнена: лок")
        XCTAssertTrue(o.offerReinstall)
        XCTAssertEqual(AppSettings.configValue("diarize_backend", root: root), "sherpa")
    }

    func testОтменаШлётСрок30() {
        let s = makeService()
        s.startInstall()
        s.cancel()
        XCTAssertEqual(s.action, .cancelling(afterCommit: false))
        XCTAssertEqual(processes[0].cancels, [30])
    }

    func testВыходОтменяетСоСроком10ИЖдётИсхода() async {
        let s = makeService()
        s.startInstall()
        var done = 0
        s.cancelForTermination { done += 1 }
        XCTAssertEqual(processes[0].cancels, [10])
        XCTAssertEqual(done, 0)
        processes[0].finish(LongProcessOutcome(code: 13, reason: .exit, cancelRequested: true, notStarted: false,
                                               stdout: "", stderrTail: "отменено", cleanupAfterCommit: false))
        await eventually("ответ выходу") { done == 1 }
        XCTAssertEqual(s.action, .outcome(.init(text: NemotronDecision.cancelledText, success: false)))
    }

    func testВыключитьПишетSherpa() async throws {
        try "sufler:\n  diarize_backend: nemotron\n".write(to: configURL, atomically: true, encoding: .utf8)
        let s = makeService()
        s.requestPlan()
        processes[0].finish(planOutcome { d in
            var offer = d["offer"] as! [String: Any]
            offer["state"] = "enabled"
            offer["disable"] = ["allowed": true, "reason": ""]
            d["offer"] = offer
        })
        await eventually("план") { if case .ready = s.plan { return true }; return false }
        s.disable()
        XCTAssertEqual(AppSettings.configValue("diarize_backend", root: root), "sherpa")
        guard case .outcome(let o) = s.action else { return XCTFail() }
        XCTAssertTrue(o.success)
        await eventually("план после выключения") { processes.count == 2 }
    }

    // MARK: 10. single-flight и две переменные

    func testДваЗапросаВоВремяПланаОдинПовтор() async {
        let s = makeService()
        var readyA = 0, readyB = 0
        let subA = s.$plan.sink { if case .ready = $0 { readyA += 1 } }
        let subB = s.$plan.sink { if case .ready = $0 { readyB += 1 } }
        defer { subA.cancel(); subB.cancel() }
        s.requestPlan()
        XCTAssertEqual(processes.count, 1)
        s.requestPlan(); s.requestPlan()
        XCTAssertEqual(processes.count, 1, "второго процесса нет")
        processes[0].finish(planOutcome())
        await eventually("повтор") { processes.count == 2 }
        processes[1].finish(planOutcome())
        await eventually("план") { readyA == 2 }
        try? await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertEqual(processes.count, 2, "ровно один повтор")
        XCTAssertEqual(readyB, 2)
    }

    func testВнешнийЗапросПриДействииНичегоНеЗапускает() {
        let s = makeService()
        s.action = .running(.install, lastLine: "x")
        s.requestPlan()
        XCTAssertTrue(processes.isEmpty)
        XCTAssertEqual(s.action, .running(.install, lastLine: "x"))
    }

    func testВнутреннийЗапросЖдётПовтора() async {
        let s = makeService()
        s.requestPlan()
        var got: NemotronPlanState?
        Task { got = await s.freshPlan() }
        try? await Task.sleep(nanoseconds: 50_000_000)
        processes[0].finish(planOutcome(planText(state: "not_installed", enable: false)))
        await eventually("повтор") { processes.count == 2 }
        XCTAssertNil(got, "план, стартовавший раньше запроса, не годится")
        processes[1].finish(planOutcome(planText(state: "ready_off", enable: true)))
        await eventually("свежий план") { got != nil }
        guard case .ready(let p) = got else { return XCTFail() }
        XCTAssertEqual(p.offer.state, "ready_off")
    }

    func testПланНеМеняетДействие() async {
        let s = makeService()
        s.action = .running(.install, lastLine: "")
        Task { _ = await s.freshPlan() }
        await eventually("план") { processes.count == 1 }
        XCTAssertEqual(s.plan, .planning(previous: nil))
        XCTAssertTrue(s.isActive)
        processes[0].finish(planOutcome())
        await eventually("план готов") { if case .ready = s.plan { return true }; return false }
        XCTAssertEqual(s.action, .running(.install, lastLine: ""))
    }

    func testКонецЗаписиОдинЗапускПлана() async {
        let s = makeService()
        recording = true
        lifecycle.send(())
        s.requestPlan()
        XCTAssertTrue(processes.isEmpty)
        XCTAssertEqual(s.plan, .none)
        try? await Task.sleep(nanoseconds: 50_000_000)
        recording = false
        lifecycle.send(())
        lifecycle.send(())
        await eventually("план по концу записи") { processes.count == 1 }
        try? await Task.sleep(nanoseconds: 100_000_000)
        XCTAssertEqual(processes.count, 1)
    }
}
