import XCTest
@testable import CharoiteApp

/// Круг n622p2-B-fix6: входы Critical — строка «ключа» в чужой кавычке или
/// flow-скобке выше, ветка `.absent` при `sufler :`/BOM, секция во flow-форме.
/// Каждый — отказ кнопки, файл байт в байт.
final class NemotronFix7Tests: XCTestCase {
    static let criticalInputs: [(name: String, text: String, expected: String?)] = [
        ("S-C1 кавычка выше ключа", "sufler:\n  prompt: \"начало\n  diarize_backend: sherpa\n  \"\n  other: 1\n", "sherpa"),
        ("S-C1 flow-список выше ключа", "sufler:\n  tags: [a,\n  diarize_backend: sherpa\n  ]\n", "sherpa"),
        ("DS-C1 кавычка до нулевого отступа", "sufler:\n  x: \"a\n  diarize_backend: sherpa\n\"\n", "sherpa"),
        ("DS-C2 вставка внутрь скаляра", "sufler:\n  quiet: true\n  x: \"a\n  b\nother: 1\"\n", nil),
        ("H-C1 sufler с пробелом, продолжение", "sufler :\n  diarize_backend: sherpa\n    cont\n  other: 1\n", "sherpa"),
        ("H-C1 sufler с пробелом, flow", "sufler :\n  tags: [a,\n  diarize_backend: sherpa\n  ]\n", "sherpa"),
        ("H-C1 BOM", "\u{FEFF}sufler:\n  diarize_backend: sherpa\n    cont\n  other: 1\n", "sherpa"),
        ("H-C2 второй блок sufler", "sufler :\n  quiet: true\n  instant: true\n", nil),
        ("H-C3 секция flow", "sufler:\n  {user_name: \"x\", quiet: true}\nllm:\n  m: 1\n", nil),
        ("H-C4 заголовок в кавычке выше", "note: \"a\nsufler:\n  diarize_backend: sherpa\n  b\"\n", "sherpa"),
    ]

    private func tempRoot() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("fix7-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url.appendingPathComponent("config"),
                                                withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    override func setUp() async throws {
        try await MainActor.run { try ConfigEditCheck.installForTest(self) }
    }

    @MainActor
    func testFix7ВходыCriticalОтказФайлБайтВБайт() async throws {
        for (name, text, expected) in Self.criticalInputs {
            let root = try tempRoot()
            let cfg = root.appendingPathComponent("config/config.yaml")
            let before = Data(text.utf8)
            try before.write(to: cfg)
            let r = await AppSettings.setConfigValueIfUnchanged("diarize_backend", expected: expected,
                                                                value: "nemotron", root: root)
            if case .written = r { XCTFail("\(name): записано") }
            XCTAssertEqual(try Data(contentsOf: cfg), before, "\(name): файл изменён")
        }
    }

    /// Ветка `.absent` при любом упоминании `sufler` — отказ построчного фильтра (Haiku C-1, C-2).
    func testFix7AbsentСУпоминаниемSuflerОтказ() {
        for text in ["sufler :\n  quiet: true\n", "\u{FEFF}sufler:\n  quiet: true\n", "\"sufler\":\n  quiet: true\n",
                     "# sufler ниже нет\nllm:\n  m: 1\n"] {
            guard case .unsupported = AppSettings.replacingIfUnchanged("diarize_backend", expected: nil,
                                                                        value: "nemotron", in: text) else {
                return XCTFail(text)
            }
        }
        // Секции нет и слова нет — дописываем, как раньше.
        XCTAssertEqual(AppSettings.replacingIfUnchanged("diarize_backend", expected: nil, value: "nemotron",
                                                        in: "llm:\n  m: 1\n"),
                       .text("llm:\n  m: 1\n\nsufler:\n  diarize_backend: \"nemotron\"\n"))
    }

    /// Проверка не запустилась, упала, не уложилась в срок или ответила не `ok` — отказ, файл цел.
    @MainActor
    func testFix7СбойПроверкиОтказ() async throws {
        let dir = try tempRoot()
        func script(_ body: String) throws -> String {
            let url = dir.appendingPathComponent("check-\(UUID().uuidString).sh")
            try "#!/bin/sh\n\(body)\n".write(to: url, atomically: true, encoding: .utf8)
            return url.path
        }
        let sh = URL(fileURLWithPath: "/bin/sh")
        let cases: [(String, ConfigEditCheck)] = [
            ("не запустилась", ConfigEditCheck(python: dir.appendingPathComponent("нет-такого"), script: "x")),
            ("упала", ConfigEditCheck(python: sh, script: try script("exit 3"))),
            ("сигнал", ConfigEditCheck(python: sh, script: try script("kill -9 $$"))),
            ("код 0, ответ не ok", ConfigEditCheck(python: sh, script: try script("echo да; exit 0"))),
            ("ok с кодом 1", ConfigEditCheck(python: sh, script: try script("echo ok; exit 1"))),
            ("не читает stdin и молчит", ConfigEditCheck(python: sh, script: try script("exit 0"))),
            ("срок", ConfigEditCheck(python: sh, script: try script("sleep 30"), timeout: 0.5)),
        ]
        for (name, check) in cases {
            ConfigEditCheck.current = check
            let root = try tempRoot()
            let cfg = root.appendingPathComponent("config/config.yaml")
            let before = Data("sufler:\n  diarize_backend: sherpa\n".utf8)
            try before.write(to: cfg)
            let started = Date()
            let r = await AppSettings.setConfigValueIfUnchanged("diarize_backend", expected: "sherpa",
                                                                value: "nemotron", root: root)
            guard case .failed(let reason) = r else { XCTFail("\(name): \(r)"); continue }
            XCTAssertTrue(reason.contains("config.yaml"), "\(name): \(reason)")
            XCTAssertEqual(try Data(contentsOf: cfg), before, "\(name): файл изменён")
            XCTAssertLessThan(Date().timeIntervalSince(started), 5, name)
        }
    }

    /// Пока шла проверка, файл изменили — `.changed`, чужая правка цела (CAS остаётся).
    @MainActor
    func testFix7ФайлИзменилиВоВремяПроверки() async throws {
        let root = try tempRoot()
        let cfg = root.appendingPathComponent("config/config.yaml")
        try Data("sufler:\n  diarize_backend: sherpa\n".utf8).write(to: cfg)
        let dir = try tempRoot()
        let url = dir.appendingPathComponent("edit.sh")
        try "#!/bin/sh\ncat >/dev/null\nprintf 'sufler:\\n  diarize_backend: other\\n' > \"\(cfg.path)\"\necho ok\n"
            .write(to: url, atomically: true, encoding: .utf8)
        ConfigEditCheck.current = ConfigEditCheck(python: URL(fileURLWithPath: "/bin/sh"), script: url.path)
        let r = await AppSettings.setConfigValueIfUnchanged("diarize_backend", expected: "sherpa",
                                                            value: "nemotron", root: root)
        XCTAssertEqual(r, .changed(current: "other"))
        XCTAssertEqual(try String(contentsOf: cfg, encoding: .utf8), "sufler:\n  diarize_backend: other\n")
    }

    /// Допуск, спрошенный вплотную перед записью, отказал — файл цел.
    @MainActor
    func testFix7ДопускПослеПроверкиОтказ() async throws {
        let root = try tempRoot()
        let cfg = root.appendingPathComponent("config/config.yaml")
        let before = Data("sufler:\n  diarize_backend: sherpa\n".utf8)
        try before.write(to: cfg)
        let r = await AppSettings.setConfigValueIfUnchanged("diarize_backend", expected: "sherpa", value: "nemotron",
                                                            root: root, recheck: { "идёт запись встречи" })
        XCTAssertEqual(r, .failed(reason: "идёт запись встречи"))
        XCTAssertEqual(try Data(contentsOf: cfg), before)
    }

    /// Боевая проверка на верной правке: время одного вызова — в отчёт.
    @MainActor
    func testFix7ВернаяПравкаПишетсяВремя() async throws {
        let root = try tempRoot()
        let cfg = root.appendingPathComponent("config/config.yaml")
        try Data("sufler:\n  diarize_backend: sherpa  # sherpa | nemotron\n  quiet: true\n".utf8).write(to: cfg)
        let started = Date()
        let r = await AppSettings.setConfigValueIfUnchanged("diarize_backend", expected: "sherpa",
                                                            value: "nemotron", root: root)
        print("FIX7-TIME проверка записи \(Int(Date().timeIntervalSince(started) * 1000)) мс")
        XCTAssertEqual(r, .written)
        XCTAssertEqual(try String(contentsOf: cfg, encoding: .utf8),
                       "sufler:\n  diarize_backend: \"nemotron\" # sherpa | nemotron\n  quiet: true\n")
    }
}
