import XCTest
@testable import CharoiteApp

/// Три версии, которые расходятся молча.
///
/// Приложение в `~/Applications`, код в рабочей папке (на нём живут демон и
/// ночной цикл) и последний выпуск на GitHub. Разошлись — снаружи ничего не
/// меняется: приложение открывается, встречи пишутся. Понимаешь это, когда
/// ловишь ошибку, которой в свежем коде нет.
final class VersionStatusTests: XCTestCase {

    func testEverythingMatches() {
        let s = VersionStatus.compare(app: "0.47.0", code: "v0.47.0", latest: "v0.47.0")
        guard case .current = s.state else { return XCTFail("ложная тревога: \(s.state)") }
    }

    /// Тег с `v` и без — одна версия. Иначе строка про расхождение горела бы
    /// у всех и всегда, а такую строку перестают читать за неделю.
    func testTagPrefixIsNotADifference() {
        XCTAssertEqual(VersionStatus.normalize("v0.47.0"), "0.47.0")
        XCTAssertEqual(VersionStatus.normalize("0.47.0-4-gabc1234"), "0.47.0")
    }

    func testNewReleaseIsOffered() {
        let s = VersionStatus.compare(app: "0.46.0", code: "v0.46.0", latest: "v0.47.0")
        guard case .updateAvailable(_, let latest) = s.state else {
            return XCTFail("новый выпуск не замечен: \(s.state)")
        }
        XCTAssertEqual(latest, "0.47.0")
    }

    /// Строковое сравнение считает «0.9.0» больше «0.47.0» и предлагает
    /// обновиться назад.
    func testVersionsCompareAsNumbers() {
        XCTAssertFalse(VersionStatus.isNewer("0.9.0", than: "0.47.0"),
                       "0.9.0 старше 0.47.0 — это откат, а не обновление")
        XCTAssertTrue(VersionStatus.isNewer("0.47.1", than: "0.47.0"))
        XCTAssertTrue(VersionStatus.isNewer("1.0.0", than: "0.47.0"))
        XCTAssertFalse(VersionStatus.isNewer("0.47.0", than: "0.47.0"))
    }

    /// Самое неприятное: приложение свежее, а демон и ночной цикл работают
    /// на коде из папки — и он другой.
    func testCodeMismatchWinsOverUpdate() {
        let s = VersionStatus.compare(app: "0.47.0", code: "v0.44.0", latest: "v0.48.0")
        guard case .codeMismatch(_, let code) = s.state else {
            return XCTFail("расхождение с рабочей папкой пропущено: \(s.state)")
        }
        XCTAssertEqual(code, "v0.44.0",
                       "новый выпуск подождёт: сначала объясняем, на чём человек работает")
    }

    /// Нет сети и нет git — не повод для тревожной строки.
    func testSilenceWhenNothingToCompare() {
        guard case .current = VersionStatus.compare(app: "0.47.0", code: nil, latest: nil).state
        else { return XCTFail("отсутствие данных выдано за проблему") }
    }
}

/// Ночной цикл, запускаемый из чужой папки.
///
/// Отдельный класс аварии: статус в `nightly.json` может быть свежим и
/// зелёным, потому что его пишет совсем другая установка. У автора launchd
/// две недели запускал скрипт из папки, оставшейся после переезда
/// репозитория, и та версия сливала дубли ядер безусловно, минуя настройку.
final class NightlyAgentPathTests: XCTestCase {

    private let root = URL(fileURLWithPath: "/Users/test/Project/charoite")

    func testOwnScriptIsFine() {
        XCTAssertFalse(NightlyStatus.agentPointsElsewhere(
            agentScript: "/Users/test/Project/charoite/scripts/nightly.sh", root: root))
    }

    func testScriptFromAnotherFolderIsCaught() {
        XCTAssertTrue(NightlyStatus.agentPointsElsewhere(
            agentScript: "/Users/test/Project/previous-checkout/scripts/nightly.sh", root: root),
            "агент запускает чужой скрипт — граф правит другая копия кода")
    }

    /// Агента нет вовсе — это «цикл не настроен», о чём говорит `never`.
    /// Ругаться здесь значило бы показывать тревогу тому, кто ничего не ставил.
    func testNoAgentIsNotAnError() {
        XCTAssertFalse(NightlyStatus.agentPointsElsewhere(agentScript: nil, root: root))
        XCTAssertFalse(NightlyStatus.agentPointsElsewhere(agentScript: "", root: root))
    }

    func testScriptPathIsReadFromPlist() throws {
        let dir = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("agents-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        let plist = """
        <?xml version="1.0" encoding="UTF-8"?>
        <plist version="1.0"><dict>
          <key>Label</key><string>ru.charoit.tier3</string>
          <key>ProgramArguments</key>
          <array><string>/bin/bash</string><string>/Users/test/Project/old/scripts/nightly.sh</string></array>
        </dict></plist>
        """
        try plist.write(to: dir.appendingPathComponent("ru.charoit.tier3.plist"),
                        atomically: true, encoding: .utf8)
        // Однострочный `<array>` — ровно тот вид, в котором plist приведён в
        // нашей же документации: перед путём стоит `/bin/bash`.
        // Посторонний агент рядом: путь берём из того plist, где реально
        // упомянут ночной скрипт, а не из первого попавшегося файла.
        try "<plist><dict><key>Label</key><string>com.other</string></dict></plist>"
            .write(to: dir.appendingPathComponent("com.other.plist"),
                   atomically: true, encoding: .utf8)

        XCTAssertEqual(NightlyStatus.agentScriptPath(inAgentsAt: dir),
                       "/Users/test/Project/old/scripts/nightly.sh")
    }
}

// №54: суточный порог превращал день выпуска в день ожидания — релиз выходил
// утром, а приложение узнавало о нём завтра. Порог четыре часа + проверка на
// активацию + ручная кнопка мимо троттла.
extension VersionStatusTests {
    func testFetchDueRespectsTheFourHourThrottle() {
        let now = Date()
        XCTAssertTrue(VersionStatusService.fetchDue(last: nil, now: now, force: false),
                      "первый запуск обязан проверить")
        XCTAssertFalse(VersionStatusService.fetchDue(
            last: now.addingTimeInterval(-3600), now: now, force: false),
            "час назад проверяли — активация не должна спамить GitHub")
        XCTAssertTrue(VersionStatusService.fetchDue(
            last: now.addingTimeInterval(-5 * 3600), now: now, force: false),
            "пять часов — пора: сутки прятали свежий выпуск до завтра")
    }

    func testManualCheckIgnoresTheThrottle() {
        let now = Date()
        XCTAssertTrue(VersionStatusService.fetchDue(
            last: now.addingTimeInterval(-60), now: now, force: true),
            "человек нажал кнопку — отвечаем сейчас, а не «уже проверял»")
    }
}
