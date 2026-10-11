import XCTest
@testable import CharoiteApp

/// Ядро тумблеров настроек (`setConfigFlag` → `settingFlag`).
///
/// Характеризация: тесты сняты на логике до выноса общего расчёта секции
/// `sufler:` и обязаны остаться зелёными после него (№622).
final class ConfigFlagTests: XCTestCase {

    func testДетиВДвапробелаВставкаВКонецСекции() {
        let text = "stt:\n  backend: gigaam\nsufler:\n  quiet: true\n  graph_dir: \"\"\nllm:\n  model: x"
        let out = AppSettings.settingFlag("cloud_engine", true, in: text)
        XCTAssertEqual(out, "stt:\n  backend: gigaam\nsufler:\n  quiet: true\n  graph_dir: \"\"\n"
                          + "  cloud_engine: true\nllm:\n  model: x")
    }

    func testДетиВЧетырепробелаОтступСохраняется() {
        let text = "sufler:\n    quiet: true\nllm:\n    model: x"
        let out = AppSettings.settingFlag("cloud_engine", false, in: text)
        XCTAssertEqual(out, "sufler:\n    quiet: true\n    cloud_engine: false\nllm:\n    model: x")
    }

    func testКомментарийПервойСтрокойСекцииНеДаётОтступ() {
        let text = "sufler:\n  # --- владелец ---\n    quiet: true"
        let out = AppSettings.settingFlag("check_updates", true, in: text)
        XCTAssertEqual(out, "sufler:\n  # --- владелец ---\n    quiet: true\n    check_updates: true")
    }

    func testСекцияБезДетейДваПробела() {
        let text = "sufler:\nllm:\n  model: x"
        let out = AppSettings.settingFlag("check_updates", true, in: text)
        XCTAssertEqual(out, "sufler:\n  check_updates: true\nllm:\n  model: x")
    }

    func testСекцииНетКлючаНетОтказ() {
        XCTAssertNil(AppSettings.settingFlag("check_updates", true, in: "llm:\n  model: x"))
    }

    func testКлючЕстьЗаменаСКомментариемСправа() {
        let text = "sufler:\n  check_updates: true   # раз в сутки к GitHub\n  quiet: true"
        let out = AppSettings.settingFlag("check_updates", false, in: text)
        // Комментарий берётся с первого « #»: лишние пробелы перед ним сжимаются.
        XCTAssertEqual(out, "sufler:\n  check_updates: false # раз в сутки к GitHub\n  quiet: true")
    }
}
