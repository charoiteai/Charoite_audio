import XCTest
@testable import CharoiteApp

/// Общий расчёт секции `sufler:` для `replacing`, `settingFlag` и условной
/// записи (№622): вставка в конец ПОСЛЕДНЕЙ секции с отступом её детей.
///
/// До правки `replacing` вставлял литерал в два пробела сразу после первой
/// строки, начинающейся с `sufler:`, — у секции с детьми в четыре пробела и у
/// `sufler: {}` YAML ломался, и демон не стартовал.
final class ConfigSectionTests: XCTestCase {

    private func lines(_ s: String) -> [String] { s.components(separatedBy: "\n") }

    /// Отступ вставленной строки и чтение `parseValue` — не только «строка есть».
    private func assertInserted(_ out: String, key: String, indent: String, value: String,
                                file: StaticString = #filePath, line: UInt = #line) {
        guard let row = lines(out).first(where: { $0.trimmingCharacters(in: .whitespaces).hasPrefix(key + ":") }) else {
            return XCTFail("ключ \(key) не вставлен:\n\(out)", file: file, line: line)
        }
        XCTAssertEqual(String(row.prefix { $0 == " " }), indent, "отступ: \(row)", file: file, line: line)
        XCTAssertFalse(row.dropFirst(indent.count).hasPrefix(" "), file: file, line: line)
        XCTAssertEqual(AppSettings.parseValue(key, in: out), value, file: file, line: line)
    }

    // MARK: suflerSection

    func testСекцияДетиВЧетыре() {
        let text = "stt:\n  a: 1\nsufler:\n    quiet: true\n    graph: x\nllm:\n  m: 1"
        XCTAssertEqual(AppSettings.suflerSection(in: lines(text)), .found(header: 2, end: 5, indent: "    "))
    }

    func testСекцияПоследняяИзДвух() {
        let text = "sufler:\n  a: 1\nllm:\n  m: 1\nsufler:\n    b: 2"
        XCTAssertEqual(AppSettings.suflerSection(in: lines(text)), .found(header: 4, end: 6, indent: "    "))
    }

    func testВложенныйSuflerНеЗаголовок() {
        XCTAssertEqual(AppSettings.suflerSection(in: lines("llm:\n  sufler:\n    a: 1")), .absent)
    }

    func testFlowИТабНеПоддерживаются() {
        guard case .unsupported = AppSettings.suflerSection(in: lines("sufler: {}")) else {
            return XCTFail("sufler: {} — flow-стиль")
        }
        guard case .unsupported = AppSettings.suflerSection(in: lines("sufler:\n\tquiet: true")) else {
            return XCTFail("таб в отступе")
        }
        // комментарий на строке заголовка — не flow
        XCTAssertEqual(AppSettings.suflerSection(in: lines("sufler:   # секция\n  a: 1")),
                       .found(header: 0, end: 2, indent: "  "))
    }

    // MARK: replacing

    func testReplacingДетиВЧетыре() {
        let text = "sufler:\n    quiet: true\nllm:\n  model: x"
        let out = AppSettings.replacing("diarize_backend", with: "nemotron", in: text)
        XCTAssertEqual(out, "sufler:\n    quiet: true\n    diarize_backend: \"nemotron\"\nllm:\n  model: x")
        assertInserted(out, key: "diarize_backend", indent: "    ", value: "nemotron")
    }

    func testReplacingДетиВДва() {
        let out = AppSettings.replacing("diarize_backend", with: "nemotron",
                                        in: "sufler:\n  quiet: true\n  graph: x")
        XCTAssertEqual(lines(out).last, "  diarize_backend: \"nemotron\"")
        assertInserted(out, key: "diarize_backend", indent: "  ", value: "nemotron")
    }

    func testReplacingКомментарийПервойСтрокой() {
        let text = "sufler:\n  # --- владелец ---\n    user_name: \"\"\nlog:\n  a: 1"
        let out = AppSettings.replacing("diarize_backend", with: "nemotron", in: text)
        assertInserted(out, key: "diarize_backend", indent: "    ", value: "nemotron")
        XCTAssertEqual(lines(out)[3], "    diarize_backend: \"nemotron\"")
    }

    func testReplacingСекцияБезДетей() {
        let out = AppSettings.replacing("diarize_backend", with: "nemotron", in: "sufler:\nllm:\n  m: 1")
        XCTAssertEqual(out, "sufler:\n  diarize_backend: \"nemotron\"\nllm:\n  m: 1")
    }

    func testReplacingПустыеСтрокиИКомментарийПередСледующейСекцией() {
        let text = "sufler:\n  quiet: true\n\n# --- журнал ---\nlog:\n  a: 1\n"
        let out = AppSettings.replacing("diarize_backend", with: "nemotron", in: text)
        XCTAssertEqual(out, "sufler:\n  quiet: true\n  diarize_backend: \"nemotron\"\n\n# --- журнал ---\nlog:\n  a: 1\n")
    }

    func testReplacingКомментарийСНулевымОтступомВнутриСекции() {
        // Раньше `setConfigFlag` считал такую строку концом секции и вставлял
        // ключ перед ней в два пробела при детях в четыре — YAML ломался.
        let text = "sufler:\n# пояснение\n    quiet: true"
        let out = AppSettings.replacing("diarize_backend", with: "nemotron", in: text)
        XCTAssertEqual(out, "sufler:\n# пояснение\n    quiet: true\n    diarize_backend: \"nemotron\"")
    }

    func testReplacingFlowИТабТекстНеТронут() {
        for text in ["stt:\n  a: 1\nsufler: {}\n", "sufler:\n\tquiet: true\n"] {
            XCTAssertEqual(AppSettings.replacing("diarize_backend", with: "nemotron", in: text), text)
        }
    }

    func testReplacingДвеСекцииВставкаВПоследнюю() {
        let text = "sufler:\n  a: 1\nllm:\n  m: 1\nsufler:\n    b: 2"
        let out = AppSettings.replacing("diarize_backend", with: "nemotron", in: text)
        XCTAssertEqual(out, text + "\n    diarize_backend: \"nemotron\"")
    }

    func testReplacingВложенныйSuflerНеЗаголовок() {
        let text = "llm:\n  sufler:\n    a: 1"
        let out = AppSettings.replacing("diarize_backend", with: "nemotron", in: text)
        XCTAssertEqual(out, text + "\nsufler:\n  diarize_backend: \"nemotron\"\n")
    }

    func testReplacingСекцииНетНоваяСекция() {
        let out = AppSettings.replacing("diarize_backend", with: "nemotron", in: "llm:\n  m: 1")
        XCTAssertEqual(out, "llm:\n  m: 1\nsufler:\n  diarize_backend: \"nemotron\"\n")
    }

    // MARK: settingFlag — намеренные изменения

    func testSettingFlagFlowОтказ() {
        XCTAssertNil(AppSettings.settingFlag("check_updates", true, in: "sufler: {}\n"))
    }

    func testSettingFlagТабОтказ() {
        XCTAssertNil(AppSettings.settingFlag("check_updates", true, in: "sufler:\n\tquiet: true"))
    }

    func testSettingFlagВложенныйSuflerНеЗаголовок() {
        XCTAssertNil(AppSettings.settingFlag("check_updates", true, in: "llm:\n  sufler:\n    a: 1"))
    }

    func testSettingFlagДвеСекцииПоследняя() {
        let text = "sufler:\n  a: 1\nllm:\n  m: 1\nsufler:\n    b: 2"
        XCTAssertEqual(AppSettings.settingFlag("check_updates", true, in: text),
                       text + "\n    check_updates: true")
    }
}
