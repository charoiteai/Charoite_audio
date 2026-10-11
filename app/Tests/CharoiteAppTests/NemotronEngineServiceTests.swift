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

    // MARK: fix1 (сверка out1): C1–C3, Z4, предусловие конфига

    private func enabledPlan(_ s: NemotronEngineService) async {
        s.requestPlan()
        processes.last!.finish(planOutcome { d in
            var offer = d["offer"] as! [String: Any]
            offer["state"] = "enabled"
            offer["disable"] = ["allowed": true, "reason": ""]
            d["offer"] = offer
        })
        await eventually("план enabled") { if case .ready = s.plan { return true }; return false }
    }

    /// C1: исход действия снимается, когда вид кнопки уходит с экрана, — иначе
    /// кнопка «Nemotron…» висит на «Сегодня» при здоровом плане навсегда.
    func testC1ИсходСнимаетсяDismissOutcome() async throws {
        try "sufler:\n  diarize_backend: nemotron\n".write(to: configURL, atomically: true, encoding: .utf8)
        let s = makeService()
        await enabledPlan(s)
        s.disable()
        guard case .outcome(let o) = s.action else { return XCTFail("нет исхода") }
        XCTAssertTrue(o.success)
        await eventually("план после выключения") { processes.count == 2 }
        processes[1].finish(planOutcome { d in
            var offer = d["offer"] as! [String: Any]
            offer["state"] = "enabled"
            offer["disable"] = ["allowed": true, "reason": ""]
            d["offer"] = offer
        })
        await eventually("план") { if case .ready = s.plan { return true }; return false }
        s.dismissOutcome()
        XCTAssertEqual(s.action, .idle)
        XCTAssertFalse(NemotronVisibleActions.make(action: s.action, plan: s.plan, busy: false, recording: false,
                                                   processRunning: s.processRunning, refusal: nil).showOnToday,
                       "здоровый enabled после ухода вида — кнопки на «Сегодня» нет")
    }

    /// C1: исход с «Переставить»/«Включить» уходом вида не снимается — иначе
    /// кнопка после неудачной пробы пропадала бы.
    func testC1ИсходСПредложениемОстаётся() {
        let s = makeService()
        let o = NemotronEngineService.ActionOutcome(text: "проверка не выполнена", success: false, offerReinstall: true)
        s.action = .outcome(o)
        s.dismissOutcome()
        XCTAssertEqual(s.action, .outcome(o))
        let failed = NemotronEngineService.ActionOutcome(text: "код 1", success: false)
        s.action = .outcome(failed)
        s.dismissOutcome()
        XCTAssertEqual(s.action, .idle)
    }

    /// C2a: «Выключить» во время записи встречи не запускает `--plan`; план — по концу записи.
    func testC2aВыключитьПриЗаписиПланаНет() async throws {
        try "sufler:\n  diarize_backend: nemotron\n".write(to: configURL, atomically: true, encoding: .utf8)
        let s = makeService()
        await enabledPlan(s)
        recording = true
        lifecycle.send(())
        try await Task.sleep(nanoseconds: 50_000_000)
        s.disable()
        XCTAssertEqual(AppSettings.configValue("diarize_backend", root: root), "sherpa")
        XCTAssertTrue(s.planDeferred, "план отложен до конца записи")
        XCTAssertEqual(processes.count, 1, "при записи второго процесса нет")
        recording = false
        lifecycle.send(())
        await eventually("план по концу записи") { processes.count == 2 }
        XCTAssertTrue(processes[1].isPlan)
    }

    /// C2b: установка кончилась кодом 0 во время записи — `--plan` не идёт,
    /// ключ не пишется, исход предлагает «Включить».
    func testC2bУстановкаКончиласьПриЗаписи() async throws {
        let s = makeService()
        s.startInstall()
        recording = true
        lifecycle.send(())
        processes[0].finish(NemotronPlanTests.outcome("готово"))
        await eventually("исход") { if case .outcome = s.action { return true }; return false }
        XCTAssertTrue(s.planDeferred, "план отложен до конца записи")
        XCTAssertEqual(processes.count, 1, "при записи плана нет")
        guard case .outcome(let o) = s.action else { return XCTFail() }
        XCTAssertFalse(o.success)
        XCTAssertTrue(o.offerEnable)
        XCTAssertTrue(o.text.hasPrefix("поставлено"), o.text)
        XCTAssertFalse(o.text.contains("включу"), "ключ после встречи сам не пишется")
        XCTAssertEqual(AppSettings.configValue("diarize_backend", root: root), "sherpa")
        recording = false
        lifecycle.send(())
        await eventually("план по концу записи") { processes.count == 2 }
    }

    /// C3: корень сменили, пока шёл `--plan` в решении, — ключ в старый корень не пишется.
    func testC3СменаКорняВОкнеПлана() async throws {
        let oldRoot = root!
        let newRoot = FileManager.default.temporaryDirectory.appendingPathComponent("nemo-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: newRoot.appendingPathComponent("config"), withIntermediateDirectories: true)
        let newConfig = "sufler:\n  diarize_backend: sherpa\n"
        try newConfig.write(to: newRoot.appendingPathComponent("config/config.yaml"), atomically: true, encoding: .utf8)
        defer {
            try? FileManager.default.removeItem(at: oldRoot)
            try? FileManager.default.removeItem(at: newRoot)
        }
        let s = makeService()
        s.startEnable()
        processes[0].finish(NemotronPlanTests.outcome("ok"))
        await eventually("план в решении") { processes.count == 2 }
        root = newRoot
        processes[1].finish(planOutcome(planText(state: "ready_off", enable: true)))
        await eventually("исход") { if case .outcome = s.action { return true }; return false }
        guard case .outcome(let o) = s.action else { return XCTFail() }
        XCTAssertFalse(o.success, "\(o.text)")
        XCTAssertEqual(AppSettings.configValue("diarize_backend", root: oldRoot), "sherpa", "старый корень не тронут")
        XCTAssertEqual(try String(contentsOf: newRoot.appendingPathComponent("config/config.yaml"), encoding: .utf8),
                       newConfig, "новый корень не тронут")
    }

    /// Z4: процесс вышел, решение ждёт `--plan` — «Отмена» не горит впустую.
    func testZ4ОтменаГаснетПослеВыходаПроцесса() async {
        let s = makeService()
        s.startEnable()
        XCTAssertTrue(s.processRunning)
        XCTAssertEqual(NemotronVisibleActions.make(action: s.action, plan: s.plan, busy: false, recording: false,
                                                   processRunning: s.processRunning, refusal: nil).cancel, .enabled)
        processes[0].finish(NemotronPlanTests.outcome("ok"))
        await eventually("план в решении") { processes.count == 2 }
        XCTAssertTrue(s.isActive)
        XCTAssertFalse(s.processRunning)
        XCTAssertEqual(NemotronVisibleActions.make(action: s.action, plan: s.plan, busy: false, recording: false,
                                                   processRunning: s.processRunning, refusal: nil).cancel, .disabled)
    }

    /// Z4: выход из приложения решён, пока шла проба, — `--plan` в решении не стартует.
    func testZ4ПриВыходеПланВРешенииНеСтартует() async throws {
        let s = makeService()
        s.startEnable()
        var done = 0
        s.terminationInProgress = true
        s.cancelForTermination { done += 1 }
        processes[0].finish(NemotronPlanTests.outcome("ok"))
        await eventually("ответ выходу") { done == 1 }
        // Исход ставится в том же ходе, что и решение: после `done` плану стартовать негде.
        guard case .outcome(let o) = s.action else { return XCTFail("нет исхода") }
        XCTAssertTrue(o.offerEnable, o.text)
        XCTAssertEqual(processes.count, 1, "плана нет")
        XCTAssertEqual(AppSettings.configValue("diarize_backend", root: root), "sherpa")
    }

    /// Сверка out1 п. 4: без `config.yaml` установка не начинается — ключ после
    /// 450 с всё равно некуда записать.
    func testБезКонфигаДействиеНеНачинается() throws {
        try FileManager.default.removeItem(at: configURL)
        let s = makeService()
        XCTAssertNotNil(s.startRefusal())
        s.startInstall(); s.startEnable()
        XCTAssertTrue(processes.isEmpty)
    }

    // MARK: fix2 (сверка fix1): допуск admit, многострочный ключ, исход по видам

    private func readyOff() -> LongProcessOutcome { planOutcome(planText(state: "ready_off", enable: true)) }

    /// Проба прошла, `--plan` в решении в полёте; к его ответу идёт запись встречи.
    private func enableUpToPlan(_ s: NemotronEngineService) async {
        s.startEnable()
        processes[0].finish(NemotronPlanTests.outcome("ok"))
        await eventually("план в решении") { processes.count == 2 }
    }

    /// Fix1 п. 1, вход A: запись началась в окне `--plan` внутри решения — ключ не пишется.
    func testFix2ЗаписьВОкнеПланаКлючНеПишется() async {
        let s = makeService()
        await enableUpToPlan(s)
        recording = true
        processes[1].finish(readyOff())
        await eventually("исход") { if case .outcome = s.action { return true }; return false }
        XCTAssertEqual(AppSettings.configValue("diarize_backend", root: root), "sherpa", "ключ записан при записи встречи")
        guard case .outcome(let o) = s.action else { return XCTFail() }
        XCTAssertFalse(o.success)
        XCTAssertTrue(o.offerEnable)
        XCTAssertTrue(o.text.hasPrefix("проверено"), "проба, а не установка: \(o.text)")
    }

    /// Fix1 п. 1, вход B (и п. 4): выход решён в окне `--plan` — ключ не пишется, план отменён со сроком выхода.
    func testFix2ВыходВОкнеПланаКлючНеПишетсяПланОтменён() async {
        let s = makeService()
        await enableUpToPlan(s)
        s.terminationInProgress = true
        var done = 0
        s.cancelForTermination { done += 1 }
        XCTAssertEqual(processes[1].cancels, [NemotronEngineService.terminationGrace], "план при выходе не отменён")
        processes[1].finish(readyOff())
        await eventually("исход") { if case .outcome = s.action { return true }; return false }
        XCTAssertEqual(AppSettings.configValue("diarize_backend", root: root), "sherpa", "ключ записан при выходе")
        await eventually("ответ выходу") { done == 1 }
    }

    /// Fix1 п. 2: повтор плана (`planAgain`) при начавшейся записи — второй `--plan` не стартует, ждёт конца записи.
    func testFix2ПовторПланаПриЗаписиОткладывается() async {
        let s = makeService()
        s.requestPlan(); s.requestPlan()
        recording = true
        lifecycle.send(())
        processes[0].finish(planOutcome())
        await eventually("повтор отложен") { s.planDeferred }
        XCTAssertEqual(processes.count, 1, "--plan стартовал при записи")
        recording = false
        lifecycle.send(())
        await eventually("план по концу записи") { processes.count == 2 }
    }

    /// Fix1 п. 3: значение ключа на нескольких строках — файл не трогаем, исход говорит почему.
    func testFix2МногострочныйКлючНеПереписывается() async throws {
        for before in ["sufler:\n  diarize_backend: >\n    sherpa\n  other: 1\n",
                       "sufler:\n  diarize_backend: \"sherpa\n    cont\"\n  other: 1\n"] {
            try before.write(to: configURL, atomically: true, encoding: .utf8)
            processes.removeAll()
            let s = makeService()
            await enableUpToPlan(s)
            processes[1].finish(readyOff())
            await eventually("исход") { if case .outcome = s.action { return true }; return false }
            XCTAssertEqual(try String(contentsOf: configURL, encoding: .utf8), before, "многострочный ключ переписан")
            guard case .outcome(let o) = s.action else { return XCTFail() }
            XCTAssertFalse(o.success, o.text)
        }
    }

    /// Fix1 п. 3: `disable()` на блочном скаляре — тоже отказ без записи.
    func testFix2ВыключитьМногострочныйКлючНеПереписывается() async throws {
        let before = "sufler:\n  diarize_backend: |\n    nemotron\n  other: 1\n"
        try before.write(to: configURL, atomically: true, encoding: .utf8)
        let s = makeService()
        await enabledPlan(s)
        s.disable()
        XCTAssertEqual(try String(contentsOf: configURL, encoding: .utf8), before)
        guard case .outcome(let o) = s.action else { return XCTFail() }
        XCTAssertFalse(o.success)
    }

    /// Fix1 п. 3: однострочный ключ пишется как раньше.
    func testFix2ОднострочныйКлючПишется() async {
        let s = makeService()
        await enableUpToPlan(s)
        processes[1].finish(readyOff())
        await eventually("исход") { if case .outcome = s.action { return true }; return false }
        XCTAssertEqual(AppSettings.configValue("diarize_backend", root: root), "nemotron")
    }

    /// Fix1 пп. 5–6: вид поповера открыт и закрыт при открытых настройках — исход
    /// остаётся; ушёл последний вид — снят.
    func testFix2ИсходСнимаетсяПоследнимВидом() async throws {
        try "sufler:\n  diarize_backend: nemotron\n".write(to: configURL, atomically: true, encoding: .utf8)
        let s = makeService()
        await enabledPlan(s)
        s.viewAppeared()                      // настройки
        s.disable()
        guard case .outcome(let o) = s.action, o.success else { return XCTFail("нет исхода") }
        s.viewAppeared(); s.viewGone()        // поповер открыт и закрыт
        XCTAssertEqual(s.action, .outcome(o), "исход стёрт чужим видом")
        s.viewGone()
        XCTAssertEqual(s.action, .idle, "последний вид ушёл — исход снят")
    }

    /// Fix1 п. 5: исход, которого никто не видел, живёт до первого показа.
    func testFix2НепоказанныйИсходЖдётПоказа() async throws {
        try "sufler:\n  diarize_backend: nemotron\n".write(to: configURL, atomically: true, encoding: .utf8)
        let s = makeService()
        await enabledPlan(s)
        s.disable()                           // ни одного вида на экране
        guard case .outcome(let o) = s.action else { return XCTFail("нет исхода") }
        s.viewAppeared()
        XCTAssertEqual(s.action, .outcome(o))
        s.viewGone()
        XCTAssertEqual(s.action, .idle)
    }

    // MARK: fix3 (сверка fix2): комментарий и CRLF после ключа, исход установки при записи

    /// Fix3 (круг fix3, Critical Sonnet/DS/Haiku): строка с `#` или из пробелов с `\r` под ключом с
    /// многострочным значением — хвост значения, а не комментарий; запись — отказ, файл цел.
    func testFix4МногострочноеЗначениеСКомментариемИCRLFНеПереписывается() {
        let cases: [(String, String)] = [
            ("A блок | с # первой строкой", "sufler:\n  diarize_backend: |\n    # note\n    sherpa\n  other: 1\n"),
            ("B кавычка с # в продолжении", "sufler:\n  diarize_backend: \"sherpa\n    # c\n    d\"\n  other: 1\n"),
            ("C блок > с голым #", "sufler:\n  diarize_backend: >\n    #\n    x\n  other: 1\n"),
            ("DS пустое значение, комментарий, sherpa", "sufler:\n  diarize_backend:\n    # голоса размечает движок\n    sherpa\n  other: 1\n"),
            ("C2 CRLF, блок > с пустой строкой \\r", "sufler:\r\n  diarize_backend: >\r\n      \r\n    sherpa\r\n  other: 1\r\n"),
            // fix5 (круг fix4, Haiku I1/I2): голая пустая строка под ключом, список на уровне ключа.
            ("I1 CRLF, блок > с голой пустой строкой \\r", "sufler:\r\n  diarize_backend: >\r\n\r\n    sherpa\r\n  other: 1\r\n"),
            ("I1 LF, блок > с пустой строкой", "sufler:\n  diarize_backend: >\n\n    sherpa\n  other: 1\n"),
            ("I2 список на отступе ключа", "sufler:\n  diarize_backend:\n  - sherpa\n  other: 1\n"),
        ]
        for (name, before) in cases {
            if case .text = AppSettings.replacingInSufler("diarize_backend", with: "nemotron", in: before) {
                XCTFail("\(name): многострочное значение переписано")
            }
            try? before.write(to: configURL, atomically: true, encoding: .utf8)
            let expected = AppSettings.parseValue("diarize_backend", in: before)
            let r = AppSettings.setConfigValueIfUnchanged("diarize_backend", expected: expected, value: "nemotron", root: root)
            if case .written = r { XCTFail("\(name): запись прошла") }
            XCTAssertEqual(try? String(contentsOf: configURL, encoding: .utf8), before, "\(name): файл изменён")
        }
    }

    // MARK: fix6 (круг fix5): белый список формы значения вместо эвристик соседа

    /// Fix6 (круг fix5, Critical Sonnet C1–C2, DS C1–C2, Haiku C-1…C-4): каждый вход — отказ,
    /// файл байт в байт.
    func testFix6ВходыКругаFix5Отказ() {
        let cases: [(String, String)] = [
            ("Sonnet C1.1 кавычка, хвост на отступе ключа", "sufler:\n  diarize_backend: \"sherpa\n  d\"\n  other: 1\n"),
            ("Sonnet C1.2 кавычка, хвост на нулевом отступе", "sufler:\n  diarize_backend: \"sherpa\nd\"\n  other: 1\n"),
            ("Sonnet C1.3 flow [a, с хвостом", "sufler:\n  diarize_backend: [a,\n  b]\n"),
            ("Sonnet C1.4 flow [ с ] на нулевом", "sufler:\n  diarize_backend: [\n]\n"),
            ("Sonnet C2 комментарий на 0 между ключом и значением", "sufler:\n  diarize_backend:\n# голоса размечает движок\n    sherpa\n  other: 1\n"),
            ("Sonnet C2' комментарий на отступе ключа", "sufler:\n  diarize_backend:\n  # голоса размечает движок\n    sherpa\n  other: 1\n"),
            ("DS C1.1 кавычка, хвост на отступе ключа", "sufler:\n  diarize_backend: \"sherpa\n  sherpa2\"\n  other: 1\n"),
            ("DS C1.2 кавычка, комментарий на 0, хвост", "sufler:\n  diarize_backend: \"sherpa\n# комментарий\n  sherpa2\"\n  other: 1\n"),
            ("DS C1.3 flow [sherpa, с хвостом", "sufler:\n  diarize_backend: [sherpa,\n  nemotron]\n  other: 1\n"),
            ("DS C2 строка ключа внутри блока |", "sufler:\n  diarize_backend: |\n    diarize_backend: sherpa\n  other: 1\n"),
            ("Haiku C-1.2 одинарная кавычка с хвостом", "sufler:\n  diarize_backend: 'sherpa\n  d'\n  other: 1\n"),
            ("Haiku C-1.3 flow [a, с хвостом и соседом", "sufler:\n  diarize_backend: [a,\n  b]\n  other: 1\n"),
            ("Haiku C-1.4 flow [ с ] на нулевом и соседом", "sufler:\n  diarize_backend: [\n]\n  other: 1\n"),
            ("Haiku C-3 список -\\t на отступе ключа", "sufler:\n  diarize_backend:\n  -\tsherpa\n  other: 1\n"),
            ("Haiku C-4 якорь и алиас", "sufler:\n  diarize_backend: &b sherpa\n  x: *b\n"),
            ("правило 3: голое слово продолжается глубже", "sufler:\n  diarize_backend: sherpa\n# c\n    cont\n  other: 1\n"),
        ]
        for (name, before) in cases {
            if case .text = AppSettings.replacingInSufler("diarize_backend", with: "nemotron", in: before) {
                XCTFail("\(name): значение сложной формы переписано")
            }
            try? before.write(to: configURL, atomically: true, encoding: .utf8)
            let expected = AppSettings.parseValue("diarize_backend", in: before)
            let r = AppSettings.setConfigValueIfUnchanged("diarize_backend", expected: expected, value: "nemotron", root: root)
            if case .written = r { XCTFail("\(name): запись прошла") }
            XCTAssertEqual(try? String(contentsOf: configURL, encoding: .utf8), before, "\(name): файл изменён")
        }
    }

    /// Fix6: форма, которую пишет приложение и образцы конфига, — `.text`, ключ записан,
    /// остальное байт в байт.
    func testFix6ФормаПриложенияПишется() {
        let cases: [(String, String, String)] = [
            ("кавычки", "sufler:\n  diarize_backend: \"nemotron\"\n  other: 1\n",
             "sufler:\n  diarize_backend: \"sherpa\"\n  other: 1\n"),
            ("голое", "sufler:\n  diarize_backend: nemotron\n  other: 1\n",
             "sufler:\n  diarize_backend: \"sherpa\"\n  other: 1\n"),
            ("одинарные кавычки", "sufler:\n  diarize_backend: 'nemotron'\n  other: 1\n",
             "sufler:\n  diarize_backend: \"sherpa\"\n  other: 1\n"),
            ("комментарий в конце", "sufler:\n  diarize_backend: nemotron  # sherpa | nemotron\n  other: 1\n",
             "sufler:\n  diarize_backend: \"sherpa\" # sherpa | nemotron\n  other: 1\n"),
            ("CRLF", "sufler:\r\n  diarize_backend: nemotron\r\n  other: 1\r\n",
             "sufler:\r\n  diarize_backend: \"sherpa\"\r\n  other: 1\r\n"),
            ("последний в секции", "sufler:\n  other: 1\n  diarize_backend: nemotron\nllm:\n  m: 1\n",
             "sufler:\n  other: 1\n  diarize_backend: \"sherpa\"\nllm:\n  m: 1\n"),
            ("последний в файле", "sufler:\n  other: 1\n  diarize_backend: nemotron\n",
             "sufler:\n  other: 1\n  diarize_backend: \"sherpa\"\n"),
            ("последний в файле без перевода строки", "sufler:\n  other: 1\n  diarize_backend: nemotron",
             "sufler:\n  other: 1\n  diarize_backend: \"sherpa\""),
            ("комментарий глубже под ключом", "sufler:\n  diarize_backend: nemotron\n    # пояснение\n  other: 1\n",
             "sufler:\n  diarize_backend: \"sherpa\"\n    # пояснение\n  other: 1\n"),
        ]
        for (name, before, after) in cases {
            XCTAssertEqual(AppSettings.replacingInSufler("diarize_backend", with: "sherpa", in: before), .text(after), name)
            try? before.write(to: configURL, atomically: true, encoding: .utf8)
            let r = AppSettings.setConfigValueIfUnchanged("diarize_backend", expected: "nemotron", value: "sherpa", root: root)
            XCTAssertEqual(r, .written, name)
            XCTAssertEqual(try? String(contentsOf: configURL, encoding: .utf8), after, "\(name): файл")
        }
    }

    /// Fix6: образцы конфига из репозитория (ru/en/zh) — ключ переписывается, остальные строки целы.
    func testFix6ОбразцыКонфигаПишутся() throws {
        let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
        for name in ["config.example.yaml", "config.example.en.yaml", "config.example.zh.yaml"] {
            let before = try String(contentsOf: repo.appendingPathComponent("config/\(name)"), encoding: .utf8)
            guard case .text(let after) = AppSettings.replacingInSufler("diarize_backend", with: "nemotron", in: before) else {
                XCTFail("\(name): отказ на образце"); continue
            }
            let b = before.components(separatedBy: "\n"), a = after.components(separatedBy: "\n")
            XCTAssertEqual(a.count, b.count, name)
            let diff = zip(b, a).filter { $0 != $1 }
            XCTAssertEqual(diff.map(\.1), ["  diarize_backend: \"nemotron\""], name)
        }
    }

    /// Fix2 п. 2: установка прошла, `--plan` упал, а к его ответу идёт запись — исход говорит «поставлено».
    func testFix3ПланУпалПриЗаписиИсходПоставлено() async {
        let s = makeService()
        s.startInstall()
        processes[0].finish(NemotronPlanTests.outcome("готово"))
        await eventually("план после установки") { processes.count == 2 }
        recording = true
        processes[1].finish(NemotronPlanTests.outcome("", code: 1, stderr: "план упал"))
        await eventually("исход") { if case .outcome = s.action { return true }; return false }
        XCTAssertEqual(AppSettings.configValue("diarize_backend", root: root), "sherpa")
        guard case .outcome(let o) = s.action else { return XCTFail() }
        XCTAssertFalse(o.success)
        XCTAssertTrue(o.offerEnable)
        XCTAssertTrue(o.text.hasPrefix("поставлено"), o.text)
    }
}
