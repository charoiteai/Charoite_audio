import XCTest
@testable import CharoiteApp

/// Обновление — единственное место, где приложение заменяет само себя.
///
/// Цена ошибки здесь выше обычной: неудачная подмена оставляет человека без
/// работающего приложения, а неудачно выбранный момент — без встречи.
final class UpdateServiceTests: XCTestCase {

    // MARK: - Когда обновляться нельзя

    /// Обновление заканчивается перезапуском. Перезапуск во время встречи
    /// обрывает запись — так уже терялись сорок минут разговора (05.08).
    func testRefusedWhileRecording() {
        XCTAssertNotNil(UpdateService.refusalReason(recording: true,
                                                    bundlePath: "/Applications/Charoite.app"),
                        "обновление во время встречи рубит запись")
    }

    /// Приложение, запущенное прямо из установщика: подменять содержимое
    /// смонтированного образа бессмысленно, том только на чтение.
    func testRefusedWhenRunningFromImage() {
        XCTAssertNotNil(UpdateService.refusalReason(
            recording: false, bundlePath: "/Volumes/Charoite 0.47.0/Charoite.app"))
    }

    func testAllowedWhenIdleAndInstalled() {
        XCTAssertNil(UpdateService.refusalReason(recording: false,
                                                 bundlePath: "/Applications/Charoite.app"))
    }

    // MARK: - Что именно ставим

    /// Файл приезжает по сети и через секунду становится приложением,
    /// которое слушает встречи. Без совпадения суммы — не ставим.
    func testChecksumMustMatch() {
        let real = String(repeating: "a", count: 64)
        XCTAssertTrue(UpdateService.checksumMatches(expected: real, actual: real))
        XCTAssertTrue(UpdateService.checksumMatches(expected: "  \(real.uppercased())\n",
                                                    actual: real),
                      "регистр и перевод строки в опубликованном файле — не расхождение")
        XCTAssertFalse(UpdateService.checksumMatches(expected: real,
                                                     actual: String(repeating: "b", count: 64)))
    }

    /// Суммы нет или она обрезана — это «проверить не удалось», а не
    /// «проверка пройдена». Молча пропустить её хуже, чем не обновиться.
    func testMissingChecksumBlocksInstall() {
        XCTAssertFalse(UpdateService.checksumMatches(expected: nil, actual: "abc"))
        XCTAssertFalse(UpdateService.checksumMatches(expected: "", actual: "abc"))
        XCTAssertFalse(UpdateService.checksumMatches(expected: "deadbeef", actual: "deadbeef"),
                       "восемь символов — не sha256; такую «сумму» подделает кто угодно")
    }

    /// Наша сумма должна совпадать с той, что кладёт в релиз `shasum` — иначе
    /// обновление будет отказывать всегда, и по совершенно верной причине.
    func testChecksumMatchesShasum() throws {
        let file = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("sum-\(UUID().uuidString).bin")
        // Больше мегабайта: сумма считается кусками, и склейка кусков — ровно
        // то место, где такая реализация обычно и врёт.
        try Data(repeating: 0x5A, count: 3 * (1 << 20) + 7).write(to: file)
        defer { try? FileManager.default.removeItem(at: file) }

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/shasum")
        p.arguments = ["-a", "256", file.path]
        let pipe = Pipe()
        p.standardOutput = pipe
        try p.run()
        let out = String(data: pipe.fileHandleForReading.readDataToEndOfFile(),
                         encoding: .utf8) ?? ""
        p.waitUntilExit()
        let expected = out.split(separator: " ").first.map(String.init) ?? ""

        XCTAssertEqual(try UpdateService.sha256(of: file), expected)
    }

    // MARK: - Подмена бандла

    /// Скрипт обязан пережить наш собственный выход и не оставить человека
    /// без приложения, если распаковка оборвётся на середине.
    func testReplacementScriptKeepsOldCopyUntilSuccess() {
        let s = UpdateService.replacementScript(pid: 4242,
                                                newApp: "/tmp/new/Charoite.app",
                                                target: "/Applications/Charoite.app")
        XCTAssertTrue(s.contains("kill -0 4242"), "подмена начинается до выхода приложения")
        XCTAssertTrue(s.contains("mv \"/Applications/Charoite.app\" \"/Applications/Charoite.app.old\""),
                      "старая копия должна уцелеть до успеха ditto")
        XCTAssertTrue(s.contains("mv \"/Applications/Charoite.app.old\" \"/Applications/Charoite.app\""),
                      "нет отката: сорвавшаяся установка оставит человека без приложения")
        XCTAssertTrue(s.contains("open \"/Applications/Charoite.app\""),
                      "после подмены приложение должно вернуться само")
    }

    /// Карантин снимаем: без этого macOS встретит обновление тем же
    /// «неизвестный разработчик», через который человек уже проходил.
    func testQuarantineIsCleared() {
        let s = UpdateService.replacementScript(pid: 1, newApp: "/tmp/a.app", target: "/tmp/b.app")
        XCTAssertTrue(s.contains("xattr -dr com.apple.quarantine"))
    }

    /// Сторож проводки: DMG и суммы обязаны собираться в релизе, иначе
    /// обновлению нечего проверять, а новому пользователю нечего скачивать.
    func testReleaseWorkflowBuildsDmgAndChecksums() throws {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()

        let wf = try String(contentsOf: root.appendingPathComponent(
            ".github/workflows/release-app.yml"), encoding: .utf8)
        XCTAssertTrue(wf.contains("make_dmg.sh"), "релиз перестал собирать установщик")
        XCTAssertTrue(wf.contains("Charoite.app.zip.sha256"),
                      "без опубликованной суммы обновление внутри приложения откажет")

        let dmg = try String(contentsOf: root.appendingPathComponent("scripts/make_dmg.sh"),
                             encoding: .utf8)
        XCTAssertTrue(dmg.contains("ln -s /Applications"),
                      "в образе нет стрелки в «Программы» — установщик теряет смысл")
        XCTAssertTrue(dmg.contains("shasum -a 256"), "суммы не считаются")
    }
}
