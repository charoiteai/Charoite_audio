import XCTest
@testable import CharoiteApp

/// Мастер первого запуска рапортовал «Сохранено», не создав config.yaml.
///
/// Два независимых дефекта складывались в один результат: на чистой машине
/// установка не начиналась вовсе, а приложение показывало успех.
///
/// 1. Образец конфига искали только в папке ДАННЫХ. В бандловой поставке он
///    лежит в `Contents/Resources/charoite/config/` — то есть в корне КОДА.
///    У нового пользователя в папке данных нет ни конфига, ни образца.
/// 2. Каталог `config/` никто не создавал, а `copyItem` в несуществующую
///    папку — ошибка. На свежей установке она случалась всегда.
///
/// Итог для человека: готовность вечно красная, «Начать слушать»
/// заблокирована, причина нигде не названа (аудит 0.46.0, P0-7).
final class FirstRunConfigTests: XCTestCase {

    private var tmp: URL!
    private let rootKey = "charoite.root"

    override func setUp() {
        super.setUp()
        tmp = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("charoite-firstrun-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        UserDefaults.standard.set(tmp.path, forKey: rootKey)
    }

    override func tearDown() {
        UserDefaults.standard.removeObject(forKey: rootKey)
        try? FileManager.default.removeItem(at: tmp)
        super.tearDown()
    }

    /// Кладёт образец туда, где он лежит в поставке рядом с данными.
    private func placeExample() throws {
        let dir = tmp.appendingPathComponent("config")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try """
        sufler:
          user_name: "владелец"
          graph_dir: ""
        """.write(to: dir.appendingPathComponent("config.example.yaml"),
                  atomically: true, encoding: .utf8)
    }

    /// Сценарий нового пользователя: папка данных пуста, конфига нет.
    func testConfigCreatedFromExample() throws {
        try placeExample()

        let ok = AppSettings.setConfigValue("user_name", "Тестовый владелец")

        XCTAssertTrue(ok, "на чистой установке конфиг обязан создаваться: "
                        + "иначе мастер настраивает в пустоту")
        let cfg = tmp.appendingPathComponent("config/config.yaml")
        XCTAssertTrue(FileManager.default.fileExists(atPath: cfg.path),
                      "config.yaml не создан — готовность останется красной навсегда")
        let text = try String(contentsOf: cfg, encoding: .utf8)
        XCTAssertTrue(text.contains("Тестовый владелец"),
                      "значение не записалось, хотя файл появился")
    }

    /// Каталог `config/` создаётся сам: до первого запуска его никто не размечает,
    /// а `copyItem` в несуществующую папку — ошибка.
    func testConfigDirectoryCreatedAutomatically() throws {
        XCTAssertFalse(
            FileManager.default.fileExists(atPath: tmp.appendingPathComponent("config").path),
            "предпосылка теста: каталога ещё нет")

        // Образец кладём после проверки — сама попытка записи должна
        // создать каталог, даже если копировать в итоге будет нечего.
        _ = AppSettings.setConfigValue("user_name", "Кто-то")

        var isDir: ObjCBool = false
        let exists = FileManager.default.fileExists(
            atPath: tmp.appendingPathComponent("config").path, isDirectory: &isDir)
        XCTAssertTrue(exists && isDir.boolValue,
                      "каталог config/ не создан — копирование образца обречено")
    }

    /// Без образца запись честно отказывает, а не делает вид, что всё хорошо.
    ///
    /// Это и есть половина дефекта: `false` возвращался и раньше, но
    /// вызывающий его не смотрел. Здесь фиксируем сам контракт.
    func testMissingExampleReportsFailure() {
        let ok = AppSettings.setConfigValue("user_name", "Кто-то")
        XCTAssertFalse(ok, "образца нет — записывать не из чего, и об этом "
                         + "обязан узнать вызывающий")
        XCTAssertNil(AppSettings.configExampleURL)
    }

    /// Образец, лежащий рядом с данными, находится.
    func testExampleFoundNextToData() throws {
        try placeExample()
        XCTAssertNotNil(AppSettings.configExampleURL,
                        "образец рядом с данными обязан находиться — это ручная установка")
    }

    /// Сторож проводки: результат записи нельзя игнорировать.
    ///
    /// Дефект был не в логике записи, а в том, что вызывающий не смотрел на
    /// ответ и показывал «Сохранено» безусловно. Если это вернётся, тест
    /// упадёт раньше пользователя.
    func testSaveResultIsChecked() throws {
        let view = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/CharoiteApp/Views/Sufler/FirstRunView.swift")
        let text = try String(contentsOf: view, encoding: .utf8)

        guard let range = text.range(of: "private func saveConfig()") else {
            return XCTFail("saveConfig не найден — тест устарел вместе с экраном")
        }
        let body = String(text[range.lowerBound...].prefix(900))

        XCTAssertTrue(body.contains("configSaveFailure"),
                      "отказ записи не показывается человеку: он снова увидит "
                    + "«Сохранено» там, где ничего не сохранилось")
        XCTAssertFalse(
            body.contains("configSaved = true\n"),
            "configSaved выставляется безусловно — это и есть починенный дефект")
    }
}
