import XCTest
@testable import CharoiteApp

/// Решение о записи `sufler.diarize_backend: nemotron` и условная запись (№622).
@MainActor
final class NemotronDecisionTests: XCTestCase {

    private func outcome(_ code: Int32 = 0, _ reason: Process.TerminationReason = .exit,
                         cancel: Bool = false, notStarted: Bool = false,
                         stdout: String = "", stderr: String = "") -> LongProcessOutcome {
        LongProcessOutcome(code: code, reason: reason, cancelRequested: cancel, notStarted: notStarted,
                           stdout: stdout, stderrTail: stderr, cleanupAfterCommit: false)
    }

    private struct Row {
        let name: String
        var outcome: LongProcessOutcome
        var rootChanged = false
        var r0: String? = "sherpa"
        var r1: String? = "sherpa"
        var allowed = true
        var cas: AppSettings.ConfigCAS? = .written
        let expect: (NemotronDecision) -> Bool
    }

    private func isStop(_ text: String? = nil, enable: Bool? = nil) -> (NemotronDecision) -> Bool {
        { d in
            guard case .stop(let t, let e, _) = d else { return false }
            if let text, t != text { return false }
            if let enable, e != enable { return false }
            return true
        }
    }

    // 4. Писать — только в строке «0, .exit, без отмены, корень тот же, R0 == R1, allowed, .written».
    func testТаблицаРешенияОЗаписи() {
        let ok = outcome()
        let rows: [Row] = [
            Row(name: "всё сошлось", outcome: ok, expect: { if case .written = $0 { return true }; return false }),
            Row(name: "ещё не писали", outcome: ok, cas: nil, expect: { $0 == .attemptWrite }),
            Row(name: "ключа не было", outcome: ok, r0: nil, r1: nil, cas: nil, expect: { $0 == .attemptWrite }),
            Row(name: "отмена при коде 0", outcome: outcome(cancel: true), expect: isStop(enable: true)),
            Row(name: "код 0 сигналом", outcome: outcome(0, .uncaughtSignal), expect: isStop()),
            Row(name: "код 1", outcome: outcome(1, stderr: "не поставлено: сеть"),
                expect: isStop("не поставлено: сеть")),
            Row(name: "корень сменили", outcome: ok, rootChanged: true, expect: isStop(enable: false)),
            Row(name: "R1 != R0", outcome: ok, r1: "nemotron", expect: isStop(enable: true)),
            Row(name: "R0 nil, R1 есть", outcome: ok, r0: nil, r1: "sherpa", expect: isStop(enable: true)),
            Row(name: "enable запрещён", outcome: ok, allowed: false, expect: isStop("нельзя", enable: false)),
            Row(name: "CAS: изменили", outcome: ok, cas: .changed(current: "Sherpa"), expect: isStop(enable: true)),
            Row(name: "CAS: файла нет", outcome: ok, cas: .failed(reason: "config.yaml ещё нет — сначала пройдите первый запуск"),
                expect: { d in
                    guard case .stop(let t, false, _) = d else { return false }
                    return t.hasSuffix("config.yaml ещё нет — сначала пройдите первый запуск")
                }),
        ]
        for row in rows {
            let d = NemotronDecision.decide(outcome: row.outcome, kind: .install, rootChanged: row.rootChanged,
                                            r0: row.r0, r1: row.r1, enableAllowed: row.allowed,
                                            enableReason: "нельзя", cas: row.cas)
            XCTAssertTrue(row.expect(d), "\(row.name): \(d)")
        }
    }

    func testТаблицаИсходВТекст() {
        let cancelled = NemotronDecision.cancelledText
        let rows: [(String, LongProcessOutcome, String)] = [
            ("отмена + 13", outcome(13, cancel: true, stderr: "отменено: получен сигнал 15"), cancelled),
            ("отмена + SIGTERM", outcome(SIGTERM, .uncaughtSignal, cancel: true), cancelled),
            ("отмена + SIGKILL", outcome(SIGKILL, .uncaughtSignal, cancel: true), cancelled),
            ("отмена + 1", outcome(1, cancel: true, stderr: "не поставлено: диск"), "не поставлено: диск"),
            ("1, stderr пуст", outcome(1, stdout: "проверяю…\nокружения движка нет: … — команда\n"),
             "окружения движка нет: … — команда"),
            ("1, stderr есть", outcome(1, stdout: "строка stdout", stderr: "проверка не выполнена: лок"),
             "проверка не выполнена: лок"),
            ("не запущен, отмена", outcome(cancel: true, notStarted: true), cancelled),
            ("12", outcome(12, stderr: "занято: идёт запись встречи"), "занято: идёт запись встречи"),
            ("сигнал без отмены", outcome(9, .uncaughtSignal), "прервано сигналом 9"),
        ]
        for (name, o, text) in rows {
            guard case .stop(let t, _, _) = NemotronDecision.outcomeFailure(o, kind: .install) ?? .attemptWrite else {
                XCTFail("\(name): нет текста"); continue
            }
            XCTAssertEqual(t, text, name)
        }
        XCTAssertNil(NemotronDecision.outcomeFailure(outcome(), kind: .install))
        guard case .stop(_, _, true) = NemotronDecision.outcomeFailure(outcome(1, stdout: "нет"), kind: .check)! else {
            return XCTFail("неудачная проба предлагает переставить")
        }
    }

    // MARK: условная запись — ядро (тест 1)

    func testCASГолоеИЗакавыченноеSherpa() {
        for raw in ["sherpa", "\"sherpa\""] {
            let text = "sufler:\n  diarize_backend: \(raw)   # sherpa | nemotron\n  quiet: true"
            let r = AppSettings.replacingIfUnchanged("diarize_backend", expected: "sherpa", value: "nemotron", in: text)
            XCTAssertEqual(r, .text("sufler:\n  diarize_backend: \"nemotron\" # sherpa | nemotron\n  quiet: true"), raw)
        }
    }

    func testCASДругоеЗначениеНеПишется() {
        for raw in ["Sherpa", "nemotron", "off"] {
            let text = "sufler:\n  diarize_backend: \(raw)"
            XCTAssertEqual(AppSettings.replacingIfUnchanged("diarize_backend", expected: "sherpa",
                                                            value: "nemotron", in: text),
                           .changed(raw), "сравнение точное, без нормализации")
        }
    }

    func testCASКлючаНетВставкаВКонецПоследнейСекции() {
        let text = "sufler:\n  a: 1\nllm:\n  m: 1\nsufler:\n    quiet: true\nlog:\n  x: 1"
        let r = AppSettings.replacingIfUnchanged("diarize_backend", expected: nil, value: "nemotron", in: text)
        XCTAssertEqual(r, .text("sufler:\n  a: 1\nllm:\n  m: 1\nsufler:\n    quiet: true\n"
                                + "    diarize_backend: \"nemotron\"\nlog:\n  x: 1"))
    }

    func testCASКлючВДвухСекцияхПравитсяПоследний() {
        let text = "sufler:\n  diarize_backend: sherpa\nsufler:\n  diarize_backend: sherpa"
        let r = AppSettings.replacingIfUnchanged("diarize_backend", expected: "sherpa", value: "nemotron", in: text)
        XCTAssertEqual(r, .text("sufler:\n  diarize_backend: sherpa\nsufler:\n  diarize_backend: \"nemotron\""))
    }

    func testCASFlowИТабОтказ() {
        for text in ["sufler: {}\n", "sufler:\n\tquiet: true\n"] {
            guard case .unsupported = AppSettings.replacingIfUnchanged("diarize_backend", expected: nil,
                                                                        value: "nemotron", in: text) else {
                return XCTFail(text)
            }
        }
    }

    // MARK: обёртка на временных корнях

    private func tempRoot() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("cas-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    private func writeConfig(_ root: URL, _ text: String) throws {
        let dir = root.appendingPathComponent("config")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try text.write(to: dir.appendingPathComponent("config.yaml"), atomically: true, encoding: .utf8)
    }

    func testCASБезФайлаОтказИФайлНеСоздан() throws {
        let root = try tempRoot()
        let mainConfig = AppSettings.charoiteRoot.appendingPathComponent("config/config.yaml")
        let before = try? Data(contentsOf: mainConfig)
        let r = AppSettings.setConfigValueIfUnchanged("diarize_backend", expected: nil, value: "nemotron", root: root)
        guard case .failed(let reason) = r else { return XCTFail("\(r)") }
        XCTAssertTrue(reason.contains("config.yaml"))
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.appendingPathComponent("config/config.yaml").path))
        XCTAssertEqual(try? Data(contentsOf: mainConfig), before, "файл в charoiteRoot не тронут")
    }

    func testCASПишетТолькоВСвойКорень() throws {
        let a = try tempRoot(), b = try tempRoot()
        let text = "sufler:\n  diarize_backend: sherpa\n"
        try writeConfig(a, text)
        try writeConfig(b, text)
        let r0 = AppSettings.configValue("diarize_backend", root: a)
        XCTAssertEqual(r0, "sherpa")
        XCTAssertEqual(AppSettings.setConfigValueIfUnchanged("diarize_backend", expected: r0,
                                                             value: "nemotron", root: a), .written)
        XCTAssertEqual(AppSettings.configValue("diarize_backend", root: a), "nemotron")
        XCTAssertEqual(try String(contentsOf: b.appendingPathComponent("config/config.yaml"), encoding: .utf8), text)
        XCTAssertEqual(AppSettings.setConfigValueIfUnchanged("diarize_backend", expected: "sherpa",
                                                             value: "nemotron", root: a),
                       .changed(current: "nemotron"))
    }

    // 11. Корень сменили между R0 и записью — ключ не пишется, новый корень не тронут.
    func testСменаКорняКлючНеПишется() throws {
        let old = try tempRoot(), new = try tempRoot()
        let text = "sufler:\n  quiet: true\n"
        try writeConfig(old, text)
        try writeConfig(new, text)
        let d = NemotronDecision.decide(outcome: outcome(), kind: .install, rootChanged: old != new,
                                        r0: nil, r1: nil, enableAllowed: true, enableReason: "", cas: nil)
        guard case .stop(let t, false, false) = d else { return XCTFail("\(d)") }
        XCTAssertTrue(t.hasPrefix("папку данных сменили"))
        XCTAssertEqual(try String(contentsOf: new.appendingPathComponent("config/config.yaml"), encoding: .utf8), text)
        XCTAssertEqual(try String(contentsOf: old.appendingPathComponent("config/config.yaml"), encoding: .utf8), text)
    }
}
