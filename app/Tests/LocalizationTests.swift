import XCTest
@testable import CharoiteApp

/// Продукт объявлен трёхъязычным, и проверять это должен тест, а не память.
///
/// Локализация делалась в два захода, и во второй раз выяснилось, что
/// пропущены ровно те экраны, которые видит новый и сломавшийся пользователь:
/// онбординг и Настройки. Хуже, недоведённость протекала в логику —
/// `statusIsProblem` искал русские подстроки в локализованном статусе, и
/// англоязычный пользователь не видел сообщения об оборванной записи.
final class LocalizationTests: XCTestCase {
    /// Файлы интерфейса, где русский литерал вне L.t — ошибка.
    private static let sources = [
        "Views/Settings/SettingsView.swift",
        "Views/Sufler/FirstRunView.swift",
        "Views/Sufler/SuflerView.swift",
        "Views/Tasks/TasksView.swift",
        "Views/LocalChat/LocalChatView.swift",
    ]

    func testUIHasNoBareRussianLiterals() throws {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Sources/CharoiteApp")

        var offenders: [String] = []
        for rel in Self.sources {
            let url = root.appendingPathComponent(rel)
            let text = try String(contentsOf: url, encoding: .utf8)
            for (i, line) in text.components(separatedBy: "\n").enumerated() {
                let code = line.trimmingCharacters(in: .whitespaces)
                guard !code.hasPrefix("//"), !code.hasPrefix("///") else { continue }
                guard code.contains("\"") else { continue }
                // Кириллица в строковом литерале допустима только внутри L.t(...)
                // или как продолжение его многострочного вызова.
                let hasCyrillic = code.range(of: #""[^"]*[А-Яа-яЁё][^"]*""#,
                                             options: .regularExpression) != nil
                guard hasCyrillic, !code.contains("L.t(") else { continue }
                // Хвосты многострочных L.t: строка начинается сразу с литерала.
                if code.hasPrefix("\"") { continue }
                offenders.append("\(rel):\(i + 1)  \(code.prefix(80))")
            }
        }
        XCTAssertTrue(offenders.isEmpty,
                      "русский текст мимо L.t — нерусский пользователь увидит его как есть:\n"
                      + offenders.joined(separator: "\n"))
    }
}

/// Разбор config.yaml — самописный, и каждая его слепая зона давала
/// молчаливый отказ: путь «не существовал», язык откатывался на русский.
final class ConfigParsingTests: XCTestCase {
    func testHandlesCRLFLineEndings() {
        // Файл с CRLF появляется сам: редактор на Windows, сетевая шара,
        // копипаст из веба. `.whitespaces` не включает \r, и значение
        // приезжало с хвостовым возвратом каретки.
        let cfg = "sufler:\r\n  graph_dir: ~/Vault\r\n  language: en\r\n"
        XCTAssertEqual(AppSettings.parseValue("graph_dir", in: cfg), "~/Vault")
        XCTAssertEqual(AppSettings.parseValue("language", in: cfg), "en")
    }

    func testKeepsHashInsideQuotedValue() {
        let cfg = "sufler:\n  graph_dir: \"~/Vault #1\"\n"
        XCTAssertEqual(AppSettings.parseValue("graph_dir", in: cfg), "~/Vault #1")
    }

    func testStripsTrailingComment() {
        let cfg = "sufler:\n  language: en   # интерфейс и документы\n"
        XCTAssertEqual(AppSettings.parseValue("language", in: cfg), "en")
    }

    func testIgnoresBlockScalar() {
        // «>» — не значение, а признак многострочного блока; раньше он
        // становился относительным путём от каталога приложения.
        let cfg = "sufler:\n  graph_dir: >\n    ~/Vault\n"
        XCTAssertNil(AppSettings.parseValue("graph_dir", in: cfg))
    }

    func testLastOccurrenceWins() {
        // stt.language и sufler.language совпадают по имени; sufler ниже.
        let cfg = "stt:\n  language: ru\nsufler:\n  language: zh\n"
        XCTAssertEqual(AppSettings.parseValue("language", in: cfg), "zh")
    }
}
