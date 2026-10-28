import XCTest
@testable import CharoiteApp

/// Посев второго прохода разбора мастером (№822) — боевой путь записи:
/// `ModelPresetPolicy.seed` → `setConfigValueIfUnchanged` → оракул PyYAML.
/// Построчное «ключа нет» расходилось с PyYAML и дописывало дубль поверх
/// ручного false (круг n822-out1, DS C1, Haiku C1).
@MainActor
final class PresetSeedTests: XCTestCase {
    override func setUp() async throws {
        try ConfigEditCheck.installForTest(self)
    }

    private let key = "graph_second_pass"

    private func root(_ text: String) throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("seed-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url.appendingPathComponent("config"),
                                                withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        try text.write(to: config(url), atomically: true, encoding: .utf8)
        return url
    }

    private func config(_ root: URL) -> URL { root.appendingPathComponent("config/config.yaml") }

    private func apply(_ id: String, _ root: URL, _ wrote: [String: String] = [:])
        async -> (wroteHere: [String: String], failure: String?) {
        let preset = ModelPresetPolicy.preset(id: id)!
        return await ModelPresetPolicy.seed(preset.seedWrites(wroteHere: wrote), wroteHere: wrote, root: root)
    }

    /// Свежая установка: ключ в шаблоне закомментирован — «Полный» сеет true в секцию sufler.
    func testШаблонПолучаетКлючВСекциюSufler() async throws {
        let r = try root("sufler:\n  graph: true\n  # graph_second_pass: true\n  graph_dir: \"\"\nlog:\n  level: info\n")
        let result = await apply("full", r)
        XCTAssertNil(result.failure)
        XCTAssertEqual(result.wroteHere, [key: "true"])
        let lines = try String(contentsOf: config(r), encoding: .utf8).components(separatedBy: "\n")
        let at = lines.firstIndex(of: "  graph_second_pass: \"true\"")
        XCTAssertNotNil(at)
        XCTAssertLessThan(at ?? .max, lines.firstIndex(of: "log:") ?? 0, "ключ — внутри sufler")
    }

    /// Ручное значение владельца — в любой форме, которую читает PyYAML, — не тронуто:
    /// обычная строка — `.changed`; формы, которых не видит построчный разбор, — отказ оракула.
    func testРучноеЗначениеНеТронуто() async throws {
        let owned = [
            "sufler:\n  graph_second_pass: false\n",
            "sufler:\n  \"graph_second_pass\": false\n",          // DS C1: ключ в кавычках
            "sufler:\n  graph_second_pass : false\n",            // DS C1: пробел перед двоеточием
            "sufler :\n  graph_second_pass: false\n",            // Haiku C1: заголовок с пробелом
            "sufler: {graph_second_pass: false}\n",              // flow-форма
            "sufler:\n  a: 1\nllm:\n  m: 1\nsufler :\n  graph_second_pass: false\n",
        ]
        for text in owned {
            let r = try root(text)
            let before = try Data(contentsOf: config(r))
            let result = await apply("full", r)
            XCTAssertEqual(try Data(contentsOf: config(r)), before, text)
            XCTAssertEqual(result.wroteHere, [:], text)
        }
    }

    /// Отказ записи человеку виден: flow-форма секции — причина, а не тихий «успех».
    func testОтказНазванПричиной() async throws {
        let r = try root("sufler: {graph: true}\n")
        let result = await apply("full", r)
        XCTAssertNotNil(result.failure)
    }

    /// «Полный» → «Лёгкий» → «Полный» в одном показе мастера: свой посев откатывается и
    /// возвращается; правка владельца между нажатиями — не наша, её не трогаем.
    func testСменаПресетаИПравкаВладельца() async throws {
        let r = try root("sufler:\n  graph: true\n")
        var wrote = await apply("full", r).wroteHere
        wrote = await apply("light", r, wrote).wroteHere
        XCTAssertEqual(AppSettings.configValue(key, root: r), "false")
        wrote = await apply("full", r, wrote).wroteHere
        XCTAssertEqual(AppSettings.configValue(key, root: r), "true")

        try "sufler:\n  graph: true\n  graph_second_pass: \"yes\"\n".write(to: config(r), atomically: true, encoding: .utf8)
        let before = try Data(contentsOf: config(r))
        let after = await apply("light", r, wrote)
        XCTAssertEqual(try Data(contentsOf: config(r)), before)
        XCTAssertEqual(after.wroteHere, [:], "значение владельца — больше не наш посев")
        XCTAssertNil(after.failure)
    }
}
