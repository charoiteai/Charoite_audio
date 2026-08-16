import XCTest
@testable import CharoiteApp

/// Приложение — второй путь наружу, и до сих пор он был открыт настежь.
///
/// Поле «Ollama» в настройках принимало любой адрес с хостом, и по нему
/// уходили чанки семантического индекса — то есть весь архив встреч — и
/// живая стенограмма. Демон на том же конфиге работать отказывался:
/// `privacy.llm_base_url` пропускает loopback, а чужой хост — только при
/// явном `llm.allow_remote: true`. Один договор, два слоя, и знал его
/// только питон: находка проходила три аудита подряд (0.45.0 P1-5,
/// 0.46.0 P0-6) и каждый раз выглядела мелкой на фоне фич.
///
/// Тест сторожит границу: адрес не на этой машине не должен становиться
/// адресом запроса молча.
final class RemoteHostPolicyTests: XCTestCase {

    private let key = "charoite.ollama"

    override func tearDown() {
        UserDefaults.standard.removeObject(forKey: key)
        super.tearDown()
    }

    // MARK: - Что считается этой машиной

    func testLoopbackHostsRecognised() {
        for host in ["localhost", "LOCALHOST", "127.0.0.1", "127.1.2.3", "::1", "0.0.0.0"] {
            XCTAssertTrue(AppSettings.isLoopbackHost(host),
                          "\(host) — это своя машина, запрос никуда не уходит")
        }
    }

    func testNonLoopbackHostsRejected() {
        // .local и имя машины — не loopback: в общей сети это чужой компьютер,
        // и стенограмма уедет к нему. Питон рассуждает так же.
        for host in ["10.0.0.5", "192.168.1.7", "ollama.local", "macbook",
                     "example.com", "127.0.0.256", "127.abc"] {
            XCTAssertFalse(AppSettings.isLoopbackHost(host),
                           "\(host) — не эта машина, нужен явный allow_remote")
        }
    }

    // MARK: - Решение по адресу из настроек

    func testEmptyFieldFallsBackToLocalhost() {
        UserDefaults.standard.set("", forKey: key)
        XCTAssertEqual(AppSettings.ollamaURL, AppSettings.defaultOllamaURL)
        XCTAssertNil(AppSettings.ollamaURLRejection,
                     "пустое поле — это не отвергнутая настройка, а её отсутствие")
    }

    func testLocalAddressPassesThrough() {
        UserDefaults.standard.set("http://127.0.0.1:11434", forKey: key)
        XCTAssertEqual(AppSettings.ollamaURL, "http://127.0.0.1:11434")
        XCTAssertNil(AppSettings.ollamaURLRejection)
    }

    func testTrailingSlashTrimmed() {
        // Иначе склейка даёт //api/tags — у части серверов это 404,
        // и «адрес не работает» списывают на политику.
        UserDefaults.standard.set("http://localhost:11434/", forKey: key)
        XCTAssertEqual(AppSettings.ollamaURL, "http://localhost:11434")
    }

    func testRemoteAddressRejectedAndReported() {
        UserDefaults.standard.set("http://192.168.1.7:11434", forKey: key)

        XCTAssertEqual(AppSettings.ollamaURL, AppSettings.defaultOllamaURL,
                       "на чужой хост запрос уходить не должен")

        let rejection = AppSettings.ollamaURLRejection
        XCTAssertNotNil(rejection,
                        "отказ обязан быть видимым: молчаливая подмена адреса — "
                      + "это вид, что настройка применена")
        XCTAssertEqual(rejection?.url, "http://192.168.1.7:11434",
                       "в предупреждении показываем тот адрес, который ввёл человек")
        XCTAssertTrue(rejection?.reason.contains("allow_remote") ?? false,
                      "причина должна называть способ разрешить, а не просто отказывать")
    }

    // MARK: - allow_remote читается так же, как его читает демон

    /// PyYAML отдаёт булево только для голого токена; `"true"` в кавычках —
    /// строка, и `privacy.py` (`is True`) отказывает. Приложение снимало
    /// кавычки и разрешало удалённый хост там, где демон падал на старте:
    /// расхождение ровно того рода, которое этот тест-класс сторожит
    /// (аудит DeepSeek 16.08). Мастер конфига пишет значения в кавычках
    /// всегда, так что случай не экзотика.
    func testQuotedTrueIsNotPermission() {
        for quoted in ["llm:\n  allow_remote: \"true\"\n", "llm:\n  allow_remote: 'true'\n",
                       "llm:\n  allow_remote: \"yes\"  # в кавычках\n"] {
            XCTAssertNil(AppSettings.parseBool("allow_remote", in: quoted),
                         "в кавычках для PyYAML это строка, а не разрешение: \(quoted)")
        }
    }

    func testBareYamlBooleansMatchPyYAML() {
        for token in ["true", "True", "TRUE", "yes", "Yes", "on", "ON"] {
            XCTAssertEqual(AppSettings.parseBool("allow_remote", in: "llm:\n  allow_remote: \(token)\n"),
                           true, token)
        }
        for token in ["false", "False", "no", "off", "OFF"] {
            XCTAssertEqual(AppSettings.parseBool("allow_remote", in: "llm:\n  allow_remote: \(token)\n"),
                           false, token)
        }
        // «1», регистр вне списка PyYAML, пусто, комментарий вместо значения — не булево
        for junk in ["1", "tRUE", "", "# позже", "truely"] {
            XCTAssertNil(AppSettings.parseBool("allow_remote", in: "llm:\n  allow_remote: \(junk)\n"), junk)
        }
        XCTAssertEqual(AppSettings.parseBool("allow_remote", in: "llm:\n  allow_remote: true   # свой сервер\n"),
                       true, "комментарий после значения — не часть значения")
        XCTAssertNil(AppSettings.parseBool("allow_remote", in: "llm:\n  base_url: http://x\n"),
                     "ключа нет — решения нет")
    }

    /// Сторож проводки: все восемь мест ходят через `ollamaURL`, и если кто-то
    /// начнёт читать UserDefaults напрямую, дыра откроется заново.
    func testNoDirectDefaultsReadsOutsideAppSettings() throws {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // CharoiteAppTests
            .deletingLastPathComponent()   // Tests
            .deletingLastPathComponent()   // app
            .appendingPathComponent("Sources/CharoiteApp")

        let files = FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil)?
            .compactMap { $0 as? URL }
            .filter { $0.pathExtension == "swift" } ?? []

        var offenders: [String] = []
        for file in files where file.lastPathComponent != "AppSettings.swift" {
            let text = try String(contentsOf: file, encoding: .utf8)
            // @AppStorage в SettingsView — это поле ввода, оно легально;
            // ищем чтение значения ради запроса.
            if text.contains("UserDefaults.standard.string(forKey: \"charoite.ollama\")") {
                offenders.append(file.lastPathComponent)
            }
        }
        XCTAssertTrue(offenders.isEmpty,
                      "адрес читается в обход политики: \(offenders.joined(separator: ", "))")
    }
}
