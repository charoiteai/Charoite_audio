import XCTest
@testable import CharoiteApp

/// План кнопки Nemotron: разбор `--plan` (тест 2), что видно (тест 3) и
/// отсутствие Enter у кнопок (тест 8) — №622.
@MainActor
final class NemotronPlanTests: XCTestCase {

    // MARK: помощники

    static func outcome(_ stdout: String, code: Int32 = 0, stderr: String = "") -> LongProcessOutcome {
        LongProcessOutcome(code: code, reason: .exit, cancelRequested: false, notStarted: false,
                           stdout: stdout, stderrTail: stderr, cleanupAfterCommit: false)
    }

    /// Фикстура с правками: `edit` получает словарь `--plan`.
    static func json(_ edit: (inout [String: Any]) -> Void = { _ in }) -> String {
        var d = try! JSONSerialization.jsonObject(with: Data(NemotronPlanFixture.emptyRoot.utf8)) as! [String: Any]
        edit(&d)
        return String(data: try! JSONSerialization.data(withJSONObject: d), encoding: .utf8)!
    }

    /// Готовый план: состояние, разрешения (`install`, `enable`, `disable`) и предупреждения.
    static func plan(_ state: String = "not_installed", install: Bool = false, enable: Bool = false,
                     disable: Bool = false, warnings: [String] = [],
                     edit: (inout [String: Any]) -> Void = { _ in }) -> NemotronPlan {
        let text = json { d in
            var offer = d["offer"] as! [String: Any]
            offer["state"] = state
            offer["install"] = ["allowed": install, "reason": install ? "" : "нельзя поставить"]
            offer["enable"] = ["allowed": enable, "reason": enable ? "" : "нельзя включить"]
            offer["disable"] = ["allowed": disable, "reason": disable ? "" : "нельзя выключить"]
            offer["warnings"] = warnings
            d["offer"] = offer
            edit(&d)
        }
        guard case .ready(let p) = NemotronPlan.parse(outcome(text)) else { fatalError("план не разобран") }
        return p
    }

    private func visible(_ plan: NemotronPlanState, action: NemotronEngineService.Action = .idle,
                         busy: Bool = false, recording: Bool = false,
                         refusal: String? = nil) -> NemotronVisibleActions {
        NemotronVisibleActions.make(action: action, plan: plan, busy: busy, recording: recording,
                                    processRunning: true, refusal: refusal)
    }

    // MARK: 2. разбор

    func testФактическийВыводПустогоКорня() {
        guard case .ready(let p) = NemotronPlan.parse(Self.outcome("строка до JSON\n" + NemotronPlanFixture.emptyRoot + "\n")) else {
            return XCTFail("фикстура не разобрана")
        }
        XCTAssertEqual(p.offer.state, "not_installed")
        XCTAssertFalse(p.offer.install.allowed)
        XCTAssertFalse(p.offer.enable.allowed)
        XCTAssertFalse(p.offer.disable.allowed)
        XCTAssertEqual(p.sizesMb, .init(environment: 601, weights: 191, voiceSet: 47))
        XCTAssertEqual(p.disk?.needMb, 1440)
        XCTAssertEqual(p.license?.name, "NVIDIA OpenMDW 1.1")
        XCTAssertEqual(p.network?.count, 4)
        XCTAssertEqual(p.offer.warnings, [])
    }

    func testConfigError() {
        let text = Self.json { d in
            d["offer"] = ["state": "config_error", "reason": "конфиг не прочитан: X",
                          "install": ["allowed": false, "reason": "конфиг не прочитан: X"],
                          "enable": ["allowed": false, "reason": "конфиг не прочитан: X"],
                          "disable": ["allowed": false, "reason": "конфиг не прочитан: X"],
                          "warnings": []]
        }
        guard case .ready(let p) = NemotronPlan.parse(Self.outcome(text)) else { return XCTFail() }
        XCTAssertEqual(p.offer.state, "config_error")
        let v = visible(.ready(p))
        XCTAssertEqual(v.detail, "конфиг не прочитан: X")
        XCTAssertNil(v.install); XCTAssertNil(v.enable); XCTAssertNil(v.disable)
    }

    func testВерхнийErrorОтказ() {
        XCTAssertEqual(NemotronPlan.parse(Self.outcome(#"{"error": "X: y"}"#, code: 1)), .failed("X: y"))
        XCTAssertEqual(NemotronPlan.parse(Self.outcome(#"{"error": "X: y"}"#)), .failed("X: y"))
    }

    func testЛишнееПолеНеЛомает() {
        let text = Self.json { d in
            d["future_field"] = ["a": 1]
            var offer = d["offer"] as! [String: Any]
            offer["attention"] = true
            d["offer"] = offer
        }
        guard case .ready = NemotronPlan.parse(Self.outcome(text)) else { return XCTFail() }
    }

    func testНетOfferОтказ() {
        let text = Self.json { $0.removeValue(forKey: "offer") }
        guard case .failed = NemotronPlan.parse(Self.outcome(text)) else { return XCTFail() }
        guard case .failed = NemotronPlan.parse(Self.outcome("не json")) else { return XCTFail() }
        guard case .failed(let t) = NemotronPlan.parse(Self.outcome("", code: 2, stderr: "трасса")) else { return XCTFail() }
        XCTAssertEqual(t, "трасса")
    }

    // MARK: 3. что видно

    /// Включённый движок с отставшим окружением: кнопка установки — «Переставить», а
    /// предупреждение подписано словами, не кодом (№758).
    func testОтставшееОкружениеПредлагаетПереставить() {
        let v = visible(.ready(Self.plan("enabled", install: true, disable: true, warnings: ["engine_outdated"])))
        XCTAssertEqual(v.install, .init(title: NemotronVisibleActions.reinstallTitle, enabled: true, note: nil))
        XCTAssertNotNil(v.consent)
        XCTAssertEqual(v.warnings.count, 1)
        XCTAssertNotEqual(v.warnings.first, "engine_outdated")
        let fresh = visible(.ready(Self.plan("not_installed", install: true)))
        XCTAssertEqual(fresh.install?.title, NemotronVisibleActions.installTitle)
        // sherpa выбран, движок готов: «Включить» и установка рядом (out1 S I2 = DS I2).
        let off = visible(.ready(Self.plan("ready_off", install: true, enable: true, warnings: ["engine_outdated"])))
        XCTAssertEqual(off.install?.title, NemotronVisibleActions.installTitle)
        XCTAssertNotNil(off.enable)
        // Установка запрещена (диск): выключенная «Переставить» с причиной, а не одно предупреждение.
        let disk = visible(.ready(Self.plan("enabled", disable: true, warnings: ["engine_outdated"])))
        XCTAssertEqual(disk.install, .init(title: NemotronVisibleActions.reinstallTitle, enabled: false,
                                           note: "нельзя поставить"))
        let quiet = visible(.ready(Self.plan("enabled", disable: true)))
        XCTAssertNil(quiet.install)
        // Ключ уже `nemotron`, веса битые — тоже переустановка (out2 H I1).
        let broken = visible(.ready(Self.plan("enabled_broken", install: true, disable: true,
                                              warnings: ["engine_outdated"])))
        XCTAssertEqual(broken.install?.title, NemotronVisibleActions.reinstallTitle)
        // Неудачная проба рядом с видимой установкой — одна кнопка, а не две (out2 DS M2).
        let failed = NemotronEngineService.Action.outcome(.init(text: "проба не прошла", success: false,
                                                                offerReinstall: true))
        let one = visible(.ready(Self.plan("ready_off", install: true, enable: true, warnings: ["engine_outdated"])),
                          action: failed)
        XCTAssertNotNil(one.install)
        XCTAssertNil(one.reinstall)
    }

    func testУстановкаСБлокомСогласия() {
        let v = visible(.ready(Self.plan(install: true)))
        XCTAssertEqual(v.install, .init(title: NemotronVisibleActions.installTitle, enabled: true, note: nil))
        let c = try! XCTUnwrap(v.consent)
        XCTAssertEqual(c.sizes, "займёт на диске: окружение 601 МБ, веса 191 МБ, набор голосов 47 МБ")
        XCTAssertEqual(c.need, "на время установки нужно свободно ≈ 1,4 ГБ")
        XCTAssertEqual(c.licenseName, "NVIDIA OpenMDW 1.1")
        XCTAssertEqual(c.licenseURL?.absoluteString, "https://huggingface.co/nvidia/Nemotron-3-Diarization/blob/main/LICENSE")
        XCTAssertEqual(c.licenseNote, "нажимая, вы принимаете условия лицензии весов")
        XCTAssertTrue(c.switchNote.contains("будет размечать Nemotron"))
        XCTAssertEqual(c.voiceSetNote, "набор голосов поставится вместе с движком")
        XCTAssertEqual(c.network.count, 4)
        XCTAssertNil(c.memoryNote, "warnings пусты")
    }

    func testПамятьТолькоПриПредупреждении() {
        let memory: (inout [String: Any]) -> Void = { $0["memory"] = ["total_mb": 16384, "warn": true, "reason": "16 ГБ — не замерено"] }
        XCTAssertNil(visible(.ready(Self.plan(install: true, edit: memory))).consent?.memoryNote)
        let v = visible(.ready(Self.plan(install: true, warnings: ["memory"], edit: memory)))
        XCTAssertEqual(v.consent?.memoryNote, "16 ГБ — не замерено")
        XCTAssertFalse(v.warnings.contains("16 ГБ — не замерено"), "не дважды")
    }

    func testВключитьБезРазмеров() {
        for state in ["ready_off", "custom_interpreter"] {
            let v = visible(.ready(Self.plan(state, install: state == "ready_off", enable: true)))
            XCTAssertEqual(v.enable?.title, NemotronVisibleActions.enableTitle, state)
            XCTAssertEqual(v.enable?.enabled, true, state)
            XCTAssertNil(v.install, "\(state): кнопки установки нет")
            XCTAssertNil(v.consent, "\(state): без размеров")
        }
    }

    func testНулевоеСлагаемоеНеПоказано() {
        let p = Self.plan(install: true) { d in d["sizes_mb"] = ["environment": 601, "weights": 191, "voice_set": 0] }
        let c = visible(.ready(p)).consent
        XCTAssertEqual(c?.sizes, "займёт на диске: окружение 601 МБ, веса 191 МБ")
        XCTAssertNil(c?.voiceSetNote)
    }

    func testСловаЗагрузкаНет() {
        let plans = [Self.plan(install: true, warnings: ["memory", "voice_set_missing", "live_nemotron"]),
                     Self.plan("ready_off", install: true, enable: true), Self.plan("enabled", disable: true)]
        for p in plans {
            for action in [NemotronEngineService.Action.idle, .running(.install, lastLine: ""), .cancelling(afterCommit: true),
                           .outcome(.init(text: "x", success: false, offerReinstall: true))] {
                let texts = visible(.ready(p), action: action).allTexts.joined(separator: " ").lowercased()
                XCTAssertFalse(texts.contains("загрузк"), texts)
            }
        }
    }

    func testНаборГолосовВнутриБлока() {
        let v = visible(.ready(Self.plan("enabled", disable: true, warnings: ["voice_set_missing"])))
        XCTAssertTrue(v.voiceSetButton)
        XCTAssertEqual(v.warnings, [NemotronPlan.voiceSetMissingText])
        XCTAssertFalse(visible(.ready(Self.plan("enabled", disable: true))).voiceSetButton)
    }

    func testЗаписьВстречи() {
        let reinstall = NemotronEngineService.Action.outcome(.init(text: "проба не прошла", success: false, offerReinstall: true))
        let rec = NemotronVisibleActions.recordingText
        let install = visible(.ready(Self.plan(install: true)), recording: true)
        XCTAssertEqual(install.install, .init(title: NemotronVisibleActions.installTitle, enabled: false, note: rec))
        let enable = visible(.ready(Self.plan("ready_off", install: true, enable: true, disable: false)),
                             action: reinstall, recording: true)
        XCTAssertEqual(enable.enable, .init(title: NemotronVisibleActions.enableTitle, enabled: false, note: rec))
        XCTAssertEqual(enable.reinstall, .init(title: NemotronVisibleActions.reinstallTitle, enabled: false, note: rec))
        let disable = visible(.ready(Self.plan("enabled", disable: true)), recording: true)
        XCTAssertEqual(disable.disable?.enabled, true, "«Выключить» — только правка конфига")
    }

    func testЗанятость() {
        let busy = EngineInstallActivity.busyText
        let v = visible(.ready(Self.plan(install: true)), busy: true)
        XCTAssertEqual(v.install, .init(title: NemotronVisibleActions.installTitle, enabled: false, note: busy))
        let e = visible(.ready(Self.plan("enabled", disable: true)), busy: true)
        XCTAssertEqual(e.disable?.enabled, false)
        XCTAssertEqual(e.disable?.note, busy)
    }

    /// Сверка fix1 п. 7: отказ службы (нет config.yaml) виден подписью у
    /// выключенной кнопки, а не тихим нажатием впустую.
    func testОтказСлужбыВыключаетКнопкиСПодписью() {
        let why = "сначала сохраните настройки — config.yaml ещё нет"
        let v = visible(.ready(Self.plan(install: true)), refusal: why)
        XCTAssertEqual(v.install, .init(title: NemotronVisibleActions.installTitle, enabled: false, note: why))
        let e = visible(.ready(Self.plan("ready_off", install: true, enable: true, disable: false)), refusal: why)
        XCTAssertEqual(e.enable, .init(title: NemotronVisibleActions.enableTitle, enabled: false, note: why))
        let ok = visible(.ready(Self.plan(install: true)))
        XCTAssertEqual(ok.install?.enabled, true)
    }

    func testУстановкаЗапрещенаНоОсмысленна() {
        let v = visible(.ready(Self.plan("broken")))
        XCTAssertEqual(v.install, .init(title: NemotronVisibleActions.installTitle, enabled: false, note: "нельзя поставить"))
        XCTAssertNil(v.consent)
        XCTAssertNil(visible(.ready(Self.plan("enabled", disable: true))).install)
    }

    func testНеизвестныйКодСостояния() {
        let v = visible(.ready(Self.plan("new_state", enable: true)))
        XCTAssertEqual(v.title, "new_state")
        XCTAssertEqual(v.enable?.enabled, true, "кнопки — по allowed")
        XCTAssertTrue(v.showOnToday)
    }

    func testПредикатСегодня() {
        let rows: [(String, NemotronPlanState, NemotronEngineService.Action, Bool)] = [
            ("enabled + disable", .ready(Self.plan("enabled", disable: true)), .idle, false),
            ("not_installed без установки", .ready(Self.plan("not_installed")), .idle, false),
            ("config_error", .ready(Self.plan("config_error")), .idle, false),
            ("enabled_broken", .ready(Self.plan("enabled_broken", disable: true)), .idle, true),
            ("backend_unknown", .ready(Self.plan("backend_unknown", disable: true)), .idle, true),
            ("неизвестный state", .ready(Self.plan("future")), .idle, true),
            ("warnings live", .ready(Self.plan("enabled", disable: true, warnings: ["live_nemotron"])), .idle, false),
            ("warnings memory", .ready(Self.plan("enabled", disable: true, warnings: ["memory"])), .idle, false),
            ("warnings voice_set_missing", .ready(Self.plan("enabled", disable: true, warnings: ["voice_set_missing"])), .idle, true),
            ("memory + enable", .ready(Self.plan("ready_off", enable: true, warnings: ["memory"])), .idle, true),
            ("install", .ready(Self.plan(install: true)), .idle, true),
            ("enable", .ready(Self.plan("ready_off", enable: true)), .idle, true),
            ("failed", .failed("X"), .idle, true),
            ("none", .none, .idle, false),
            ("planning(nil)", .planning(previous: nil), .idle, false),
            ("planning(enabled)", .planning(previous: Self.plan("enabled", disable: true)), .idle, false),
            ("planning(install)", .planning(previous: Self.plan(install: true)), .idle, true),
            ("running + planning", .planning(previous: nil), .running(.install, lastLine: ""), true),
            ("cancelling + planning", .planning(previous: nil), .cancelling(afterCommit: false), true),
            ("outcome + planning", .planning(previous: nil), .outcome(.init(text: "ok", success: true)), true),
            ("outcome + enabled", .ready(Self.plan("enabled", disable: true)), .outcome(.init(text: "ok", success: true)), true),
        ]
        for (name, plan, action, expected) in rows {
            XCTAssertEqual(visible(plan, action: action).showOnToday, expected, name)
        }
    }

    func testДействиеИОтмена() {
        let r = visible(.ready(Self.plan(install: true)), action: .running(.install, lastLine: "пакеты из …"))
        XCTAssertEqual(r.progress, "пакеты из …")
        XCTAssertEqual(r.cancel, .enabled)
        XCTAssertNil(r.install, "во время действия кнопок плана нет")
        let c = visible(.none, action: .cancelling(afterCommit: true))
        XCTAssertEqual(c.progress, "движок уже поставлен, доделываю уборку")
        XCTAssertEqual(c.cancel, .disabled)
    }

    func testЗаписьИдётПланОтложен() {
        XCTAssertEqual(visible(.none, recording: true).detail, NemotronVisibleActions.deferredText)
        XCTAssertNil(visible(.none).detail)
    }

    /// Пин словаря: восемь кодов `state` и три кода `warnings` из абзаца
    /// `offer` в `docs/ru/DIARIZATION.md` — все с заголовком и подписью.
    func testСловарьСостоянийИзДокумента() throws {
        let doc = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent("docs/ru/DIARIZATION.md")
        let text = try String(contentsOf: doc, encoding: .utf8)
        let start = try XCTUnwrap(text.range(of: "`offer` — одно решение"))
        let paragraph = String(text[start.lowerBound...]).components(separatedBy: "\n\n").first ?? ""
        let codes = Set(try NSRegularExpression(pattern: "`([a-z_]+)`")
            .matches(in: paragraph, range: NSRange(paragraph.startIndex..., in: paragraph))
            .map { String(paragraph[Range($0.range(at: 1), in: paragraph)!]) })
        for state in NemotronPlan.knownStates {
            XCTAssertTrue(codes.contains(state), "\(state) нет в документе")
            XCTAssertNotEqual(NemotronPlan.stateTitle(state), state, "\(state) без заголовка")
        }
        let documentedStates = codes.intersection(["not_installed", "broken", "ready_off", "enabled", "enabled_broken",
                                                   "custom_interpreter", "backend_unknown", "config_error"])
        XCTAssertEqual(documentedStates.count, 8)
        let p = Self.plan(edit: { $0["memory"] = ["total_mb": 8192, "warn": true, "reason": "мало памяти"] })
        for code in NemotronPlan.warningCodes {
            XCTAssertTrue(codes.contains(code), "\(code) нет в документе")
            XCTAssertNotEqual(p.warningText(code), code, "\(code) без подписи")
        }
        // Документ не ушёл вперёд словаря: каждый код из перечня абзаца известен коду.
        let listed = try NSRegularExpression(pattern: "`([a-z_]+)` \\(")
            .matches(in: paragraph, range: NSRange(paragraph.startIndex..., in: paragraph))
            .map { String(paragraph[Range($0.range(at: 1), in: paragraph)!]) }
        for code in listed {
            XCTAssertTrue(NemotronPlan.knownStates.contains(code) || NemotronPlan.warningCodes.contains(code),
                          "\(code) из документа неизвестен коду")
        }
    }

    // MARK: 8. Enter не у кнопок Nemotron

    func testУКнопокNemotronНетEnter() throws {
        let views = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Sources/CharoiteApp/Views")
        // Поповер «Сегодня» (`NemotronTodayButton`) живёт в том же файле.
        let text = try String(contentsOf: views.appendingPathComponent("Settings/NemotronEngineControl.swift"), encoding: .utf8)
        XCTAssertTrue(text.contains("struct NemotronTodayButton"), "поповер переехал — сканировать его файл")
        XCTAssertFalse(text.contains(".keyboardShortcut(.defaultAction)"))
        XCTAssertFalse(text.contains(".keyboardShortcut(.return"))
    }
}
