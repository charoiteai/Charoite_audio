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

    /// Первая проверка не защищает от встречи, начавшейся во время загрузки.
    /// Вторая обязана стоять после последнего await и до запуска helper; тогда
    /// MainActor не пропустит Start внутрь критического синхронного участка.
    func testInstallRechecksRecordingImmediatelyBeforeReplacement() throws {
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let source = try String(contentsOf: root.appendingPathComponent(
            "app/Sources/CharoiteApp/Services/UpdateService.swift"), encoding: .utf8)
        let lastAwait = try XCTUnwrap(source.range(of: "let published = try? await string"))
        let afterAwait = source[lastAwait.upperBound...]
        let preflight = try XCTUnwrap(afterAwait.range(of:
            "Self.refusalReason(recording: SuflerService.shared.hasActiveLifecycle"))
        let launch = try XCTUnwrap(afterAwait.range(of: "try replace.run()"))

        XCTAssertLessThan(preflight.lowerBound, launch.lowerBound)
        XCTAssertFalse(afterAwait[preflight.lowerBound..<launch.upperBound].contains("await"),
                       "между повторным preflight и helper снова появилось окно для Start")
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
        let s = UpdateService.replacementScript()
        XCTAssertTrue(s.contains("kill -0 \"$pid\""), "подмена начинается до выхода приложения")
        XCTAssertTrue(s.contains("mv \"$target\" \"${target}.old\""),
                      "старая копия должна уцелеть до успеха ditto")
        XCTAssertTrue(s.contains("mv \"${target}.old\" \"$target\""),
                      "нет отката: сорвавшаяся установка оставит человека без приложения")
        XCTAssertTrue(s.contains("open \"$target\""),
                      "после подмены приложение должно вернуться само")
    }

    /// Истечение десяти секунд не доказывает смерть приложения. При живом
    /// PID helper обязан выйти ДО первой операции с установленным бандлом.
    func testReplacementScriptFailsClosedWhileAppIsAlive() throws {
        let s = UpdateService.replacementScript()
        let guardRange = try XCTUnwrap(s.range(of: """
        if kill -0 "$pid" 2>/dev/null; then
          exit 75
        fi
        """))
        let replaceRange = try XCTUnwrap(s.range(of: "mv \"$target\" \"${target}.old\""))

        XCTAssertLessThan(guardRange.lowerBound, replaceRange.lowerBound,
                          "helper меняет бандл до повторной проверки PID")
    }

    /// Пути передаются отдельными argv: bash не должен повторно разбирать
    /// кавычки, `$()` или перевод строки внутри имени приложения.
    func testReplacementArgumentsKeepPathsAsData() {
        let newApp = "/tmp/new/Charoite$(open -a Calculator).app"
        let target = "/Applications/Charoite \"Work\".app\ncopy"

        XCTAssertEqual(UpdateService.replacementArguments(
            script: "/tmp/replace.sh",
            pid: 4242,
            newApp: newApp,
            target: target
        ), ["/tmp/replace.sh", "4242", newApp, target])
        XCTAssertFalse(UpdateService.replacementScript().contains("Calculator"))
    }

    /// Карантин снимаем: без этого macOS встретит обновление тем же
    /// «неизвестный разработчик», через который человек уже проходил.
    func testQuarantineIsCleared() {
        let s = UpdateService.replacementScript()
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
