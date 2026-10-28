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
        // refused: true — отказ оракула или формы с причиной; false — `.changed`, молча; nil — не судим.
        let owned: [(String, Bool?)] = [
            ("sufler:\n  graph_second_pass: false\n", false),
            ("sufler:\n  \"graph_second_pass\": false\n", true),   // DS C1: ключ в кавычках
            ("sufler:\n  graph_second_pass : false\n", true),     // DS C1: пробел перед двоеточием
            ("sufler :\n  graph_second_pass: false\n", nil),      // Haiku C1: заголовок с пробелом
            ("sufler: {graph_second_pass: false}\n", true),       // flow-форма
            ("sufler:\n  a: 1\nllm:\n  m: 1\nsufler :\n  graph_second_pass: false\n", nil),
        ]
        for (text, refused) in owned {
            let r = try root(text)
            let before = try Data(contentsOf: config(r))
            let result = await apply("full", r)
            XCTAssertEqual(try Data(contentsOf: config(r)), before, text)
            XCTAssertEqual(result.wroteHere, [:], text)
            if let refused { XCTAssertEqual(result.failure != nil, refused, text) }
        }
    }

    /// Настоящие шаблоны, которые мастер копирует на свежей машине, — посев ложится и проходит оракул.
    func testПосевВНастоящиеШаблоны() async throws {
        for name in ["config.example.yaml", "config.example.en.yaml", "config.example.zh.yaml"] {
            let template = ConfigEditCheck.repo.appendingPathComponent("config/\(name)")
            let r = try root(try String(contentsOf: template, encoding: .utf8))
            let result = await apply("full", r)
            XCTAssertNil(result.failure, name)
            XCTAssertEqual(AppSettings.configValue(key, root: r), "true", name)
        }
    }

    /// Проверка, которая перед настоящим оракулом дописывает комментарий в config.yaml —
    /// чужая запись (имя, папка графа) в окне проверки. `once` — только при первом вызове.
    private func drifting(once: Bool) throws -> ConfigEditCheck {
        guard let py = ConfigEditCheck.testPython else { throw XCTSkip("нет python с PyYAML") }
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("drift-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: dir) }
        let mark = dir.appendingPathComponent("done").path
        let guardLine = once ? "[ -e '\(mark)' ] || " : ""
        let script = dir.appendingPathComponent("drift.sh")
        try """
        \(guardLine){ printf '# дрейф\\n' >> "$CHAROITE_ROOT/config/config.yaml"; : > '\(mark)'; }
        exec '\(py.path)' '\(ConfigEditCheck.repo.path)/scripts/install_engine.py' "$@"
        """.write(to: script, atomically: true, encoding: .utf8)
        return ConfigEditCheck(python: URL(fileURLWithPath: "/bin/sh"), script: script.path)
    }

    /// Байты сдвинула чужая запись, значение то же — посев повторяется и ложится (n822-out2, DS I1).
    func testДрейфБайтовНеПутаетсяСПравкойВладельца() async throws {
        let r = try root("sufler:\n  graph: true\n")
        ConfigEditCheck.current = try drifting(once: true)
        let result = await apply("full", r)
        XCTAssertNil(result.failure)
        XCTAssertEqual(result.wroteHere, [key: "true"])
        let text = try String(contentsOf: config(r), encoding: .utf8)
        XCTAssertTrue(text.contains("# дрейф"))
        XCTAssertEqual(AppSettings.configValue(key, root: r), "true")
    }

    /// Файл меняют на каждой попытке: посев не лёг, причина названа, свой посев мастер не забывает.
    func testДрейфБезКонцаНеСтираетСвойПосев() async throws {
        let r = try root("sufler:\n  graph: true\n")
        let wrote = await apply("full", r).wroteHere
        ConfigEditCheck.current = try drifting(once: false)
        let result = await apply("light", r, wrote)
        XCTAssertNotNil(result.failure)
        XCTAssertEqual(result.wroteHere, [key: "true"], "посев всё ещё наш — следующий «Лёгкий» откатит")
        XCTAssertEqual(AppSettings.configValue(key, root: r), "true")
    }

    /// Отказ проверки не стирает память о своём посеве.
    func testОтказНеСтираетСвойПосев() async throws {
        let r = try root("sufler:\n  graph: true\n")
        let wrote = await apply("full", r).wroteHere
        ConfigEditCheck.current = ConfigEditCheck(python: URL(fileURLWithPath: "/usr/bin/false"), script: "/dev/null")
        let result = await apply("light", r, wrote)
        XCTAssertNotNil(result.failure)
        XCTAssertEqual(result.wroteHere, [key: "true"])
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
