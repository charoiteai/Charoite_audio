import XCTest
@testable import CharoiteApp

/// Запись двух обязательных полей конфига из мастера первого запуска.
///
/// До этого приложение конфиг только читало, и человек правил YAML в
/// редакторе — первый же шаг установки, который отпугивал. Разбор чистой
/// функцией, поэтому и правило проверяется тестом, а не первым запуском.
final class ConfigWriteTests: XCTestCase {

    func testЗначениеЗаменяетсяВНужнойСтроке() {
        let text = """
        sufler:
          user_name: ""           # ваше имя
          graph_dir: ""
        """
        let out = AppSettings.replacing("user_name", with: "Мария", in: text)
        XCTAssertTrue(out.contains("user_name: \"Мария\""), out)
        XCTAssertTrue(out.contains("graph_dir: \"\""), "соседний ключ не должен пострадать")
    }

    func testКомментарийСправаСохраняется() {
        let text = "sufler:\n  user_name: \"\"           # ваше имя: метка микрофона"
        let out = AppSettings.replacing("user_name", with: "Мария", in: text)
        XCTAssertTrue(out.contains("# ваше имя: метка микрофона"),
                      "комментарий объясняет поле человеку — терять его нельзя")
    }

    func testПутьСПробеломПолучаетКавычки() {
        let text = "sufler:\n  graph_dir: \"\""
        let out = AppSettings.replacing("graph_dir", with: "/Users/a/My Vault", in: text)
        XCTAssertTrue(out.contains("graph_dir: \"/Users/a/My Vault\""),
                      "без кавычек путь с пробелом молча ломает YAML")
    }

    func testОтступСекцииСохраняется() {
        let text = "sufler:\n    user_name: \"\""
        let out = AppSettings.replacing("user_name", with: "Мария", in: text)
        XCTAssertTrue(out.contains("    user_name: \"Мария\""), "отступ определяет вложенность YAML")
    }

    func testОтсутствующийКлючУходитВСекциюSufler() {
        let text = "stt:\n  backend: gigaam\nsufler:\n  quiet: true"
        let out = AppSettings.replacing("user_name", with: "Мария", in: text)
        let lines = out.components(separatedBy: "\n")
        guard let i = lines.firstIndex(where: { $0.contains("user_name") }) else {
            return XCTFail("ключ не добавлен")
        }
        XCTAssertTrue(lines[i].hasPrefix("  "), "ключ в корне YAML ничего не настроит")
        XCTAssertTrue(lines[i - 1].hasPrefix("sufler:"), "ключ обязан лечь в секцию sufler")
    }

    func testПоследнееВхождениеПобеждает() {
        // language есть и в stt, и в sufler — правим тот, что ниже,
        // ровно как его читает parseValue.
        let text = "stt:\n  language: ru\nsufler:\n  language: ru"
        let out = AppSettings.replacing("language", with: "en", in: text)
        let lines = out.components(separatedBy: "\n")
        XCTAssertTrue(lines[1].contains("language: ru"), "секцию stt не трогаем")
        XCTAssertTrue(lines[3].contains("language: \"en\""), "правим секцию sufler")
    }
}

extension ConfigWriteTests {

    /// Найдено на живом экране мастера 07.08: в поле имени приезжало
    /// `Мария"           # метка вашего микрофона`. Приложение пишет значения
    /// в кавычках всегда (путь с пробелом иначе ломает YAML), а парсер у
    /// закавыченного значения комментарий не отсекал вовсе.
    func testЗакавыченноеЗначениеСКомментариемЧитаетсяЧисто() {
        let text = "sufler:\n  user_name: \"Мария\"           # метка вашего микрофона"
        XCTAssertEqual(AppSettings.parseValue("user_name", in: text), "Мария")
    }

    func testРешёткаВнутриКавычекОстаётсяЧастьюЗначения() {
        let text = "sufler:\n  graph_dir: \"/Users/a/Vault #1\"   # рабочий граф"
        XCTAssertEqual(AppSettings.parseValue("graph_dir", in: text), "/Users/a/Vault #1")
    }

    func testЗаписьИЧтениеСходятся() {
        // Полный круг: то, что записали, обязано прочитаться тем же.
        let text = "sufler:\n  user_name: \"\"   # ваше имя\n  graph_dir: \"\""
        let afterName = AppSettings.replacing("user_name", with: "Анна Петрова", in: text)
        let afterBoth = AppSettings.replacing("graph_dir", with: "/Users/a/My Vault",
                                              in: afterName)
        XCTAssertEqual(AppSettings.parseValue("user_name", in: afterBoth), "Анна Петрова")
        XCTAssertEqual(AppSettings.parseValue("graph_dir", in: afterBoth), "/Users/a/My Vault")
    }
}
