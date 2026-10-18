import XCTest
@testable import CharoiteApp

/// Ночь у пользователя ставит приложение: четыре слота и CHAROITE_SLOT=1, по
/// которому nightly.sh зовёт гейт. Без переменной гейт молчит, и каждый слот
/// переделывал бы уже сделанную ночь.
final class NightlyScheduleTests: XCTestCase {
    func testJobHasFourSlotsAndTheGateFlag() throws {
        let job = NightlyAgent.job(script: "/x/scripts/nightly.sh", logPath: "/x/logs/nightly.log")
        let slots = try XCTUnwrap(job["StartCalendarInterval"] as? [[String: Int]])
        XCTAssertEqual(slots.map { [$0["Hour"]!, $0["Minute"]!] },
                       [[19, 0], [20, 0], [21, 0], [4, 15]])
        XCTAssertEqual(job["EnvironmentVariables"] as? [String: String], ["CHAROITE_SLOT": "1"])
        XCTAssertEqual(job["Label"] as? String, "ai.charoite.nightly")
        XCTAssertEqual(job["ProgramArguments"] as? [String], ["/bin/bash", "/x/scripts/nightly.sh"])
    }

    /// launchd принимает только то, что сериализуется в plist.
    func testJobSerializesToPlist() throws {
        let job = NightlyAgent.job(script: "/a & b/nightly.sh", logPath: "/a/l.log")
        let data = try PropertyListSerialization.data(fromPropertyList: job, format: .xml, options: 0)
        let back = try XCTUnwrap(PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any])
        XCTAssertEqual((back["StartCalendarInterval"] as? [Any])?.count, 4)
        XCTAssertEqual(back["ProgramArguments"] as? [String], ["/bin/bash", "/a & b/nightly.sh"])
    }

    /// Plist прежней версии: один 04:15 и без CHAROITE_SLOT.
    private func oldJob(script: String = "/x/scripts/nightly.sh") -> [String: Any] {
        ["Label": NightlyAgent.label,
         "ProgramArguments": ["/bin/bash", script],
         "StartCalendarInterval": ["Hour": 4, "Minute": 15],
         "StandardOutPath": "/x/logs/nightly.log",
         "StandardErrorPath": "/x/logs/nightly.log"]
    }

    private let expected = NightlyAgent.job(script: "/x/scripts/nightly.sh", logPath: "/x/logs/nightly.log")

    /// Включивший ночь до слотов после обновления получает новые слоты.
    func testOldPlistIsMigrated() {
        XCTAssertTrue(NightlyAgent.needsMigration(installed: oldJob(), expected: expected, nightRunning: false))
    }

    /// Plist, прошедший через диск, равен ожидаемому — иначе миграция
    /// перегружала бы агента на каждом старте.
    func testCurrentPlistRoundTripNeedsNothing() throws {
        let data = try PropertyListSerialization.data(fromPropertyList: expected, format: .xml, options: 0)
        let back = try XCTUnwrap(PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any])
        XCTAssertFalse(NightlyAgent.needsMigration(installed: back, expected: expected, nightRunning: false))
    }

    /// Агент другой установки не подменяем молча.
    func testForeignScriptIsLeftAlone() {
        XCTAssertFalse(NightlyAgent.needsMigration(installed: oldJob(script: "/other/scripts/nightly.sh"),
                                                   expected: expected, nightRunning: false))
    }

    /// `unload` посреди ночи оборвал бы прогон.
    func testRunningNightIsNotTouched() {
        XCTAssertFalse(NightlyAgent.needsMigration(installed: oldJob(), expected: expected, nightRunning: true))
    }

    func testNoAgentNoMigration() {
        XCTAssertFalse(NightlyAgent.needsMigration(installed: nil, expected: expected, nightRunning: false))
    }

    // ─ выходной круг 2 №630: свои ключи, симлинк, вторая метка, порядок записи ─

    /// Свои ключи окружения человека переживают миграцию, CHAROITE_SLOT
    /// добавляется; путь лога — наш (круг 3: /tmp до 16.08 не консервируем).
    func testReconcileKeepsUserEnvOwnsLogs() {
        var old = oldJob()
        old["EnvironmentVariables"] = ["CHAROITE_ROOT": "/data", "NIGHTLY_MAX_H": "2"]
        old["StandardOutPath"] = "/mine.log"
        XCTAssertTrue(NightlyAgent.needsMigration(installed: old, expected: expected, nightRunning: false))
        let out = NightlyAgent.reconciled(installed: old, expected: expected)
        XCTAssertEqual(out["EnvironmentVariables"] as? [String: String],
                       ["CHAROITE_ROOT": "/data", "NIGHTLY_MAX_H": "2", "CHAROITE_SLOT": "1"])
        XCTAssertEqual(out["StandardOutPath"] as? String, expected["StandardOutPath"] as? String)
        XCTAssertFalse(NightlyAgent.needsMigration(installed: out, expected: expected, nightRunning: false))
    }

    /// Корень через симлинк — та же установка, миграция идёт.
    func testSymlinkedRootIsOurs() throws {
        let dir = try tmpDir()
        let real = dir.appendingPathComponent("real")
        try FileManager.default.createDirectory(at: real.appendingPathComponent("scripts"),
                                                withIntermediateDirectories: true)
        // путь разрешается только у существующего файла — скрипт есть у любой установки
        FileManager.default.createFile(atPath: real.path + "/scripts/nightly.sh", contents: gatedScript)
        FileManager.default.createFile(atPath: real.path + "/scripts/night_gate.py", contents: gateBody)
        let link = dir.appendingPathComponent("link")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: real)
        let want = NightlyAgent.job(script: real.path + "/scripts/nightly.sh", logPath: "/l")
        XCTAssertTrue(NightlyAgent.needsMigration(installed: oldJob(script: link.path + "/scripts/nightly.sh"),
                                                  expected: want, nightRunning: false))
    }

    /// Старый plist переписан, свои ключи на месте, порядок unload → load.
    func testInstallOverOldPlistKeepsEnv() throws {
        let (root, agents) = try fixture()
        var old = oldJob(script: root.path + "/scripts/nightly.sh")
        old["EnvironmentVariables"] = ["NIGHTLY_MAX_H": "2"]
        let target = NightlyAgent.plistURL(in: agents)
        try write(old, to: target)
        var calls: [[String]] = []
        XCTAssertEqual(NightlyAgent.install(root: root, agentsDir: agents) { calls.append($0) }, .done)
        XCTAssertEqual(calls.map { $0[0] }, ["unload", "load"])
        let back = try read(target)
        XCTAssertEqual((back["StartCalendarInterval"] as? [Any])?.count, 4)
        XCTAssertEqual(back["EnvironmentVariables"] as? [String: String],
                       ["NIGHTLY_MAX_H": "2", "CHAROITE_SLOT": "1"])
        XCTAssertFalse(FileManager.default.fileExists(
            atPath: agents.appendingPathComponent(".\(NightlyAgent.label).plist.new").path))
    }

    /// Запись не удалась — агент не выгружается: раньше unload шёл до записи.
    func testFailedWriteLeavesAgentLoaded() throws {
        let (root, agents) = try fixture()
        let target = NightlyAgent.plistURL(in: agents)
        try write(oldJob(script: root.path + "/scripts/nightly.sh"), to: target)
        try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: agents.path)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: agents.path) }
        var calls: [[String]] = []
        try XCTSkipIf(geteuid() == 0, "root пишет и в каталог 0555")
        guard case .failed = NightlyAgent.install(root: root, agentsDir: agents, launchctl: { calls.append($0) })
        else { return XCTFail("запись в каталог только для чтения прошла") }
        XCTAssertTrue(calls.isEmpty, "\(calls)")
        XCTAssertEqual((try read(target))["StartCalendarInterval"] as? [String: Int], ["Hour": 4, "Minute": 15])
    }

    // ─ выходные круги 3–4 №630: типы окружения, обёртка, лог, пробел, Program, метка ─

    /// `<integer>` в окружении не стирает строковые ключи: приведение к
    /// `[String: String]` роняло весь словарь.
    func testIntegerEnvValueKeepsOtherKeys() throws {
        let (root, agents) = try fixture()
        var old = oldJob(script: root.path + "/scripts/nightly.sh")
        old["EnvironmentVariables"] = ["CHAROITE_ROOT": "/data", "NIGHTLY_MAX_H": 2] as [String: Any]
        let target = NightlyAgent.plistURL(in: agents)
        try write(old, to: target)
        XCTAssertEqual(NightlyAgent.install(root: root, agentsDir: agents) { _ in }, .done)
        let env = try XCTUnwrap((try read(target))["EnvironmentVariables"] as? [String: Any])
        XCTAssertEqual(env["CHAROITE_ROOT"] as? String, "/data")
        XCTAssertEqual(env["NIGHTLY_MAX_H"] as? Int, 2)
        XCTAssertEqual(env["CHAROITE_SLOT"] as? String, "1")
    }

    /// Обёртка `caffeinate` (и флаги с аргументом, `-t <сек>`) — ручной агент:
    /// разбирать чужие флаги не берёмся, миграция и установка его не трогают (круг 11).
    func testWrapperIsManualNotMigrated() throws {
        let script = "/x/scripts/nightly.sh"
        for wrapped in [["/usr/bin/caffeinate", "-i", "/bin/bash", script],
                        ["/usr/bin/caffeinate", "-t", script],
                        ["/usr/bin/arch", "-arm64", "/bin/bash", script]] {
            var old = oldJob(script: script)
            old["ProgramArguments"] = wrapped
            XCTAssertEqual(NightlyAgent.classify(old, script: script), .manual("04:15"), "\(wrapped)")
            XCTAssertFalse(NightlyAgent.needsMigration(installed: old, expected: expected, nightRunning: false),
                           "\(wrapped)")
        }
    }

    /// `Disabled=false` — агент уже включён: ключ не повод для миграции (круг 11).
    func testDisabledFalseDoesNotMigrate() {
        var cur = expected
        cur["Disabled"] = false
        XCTAssertFalse(NightlyAgent.needsMigration(installed: cur, expected: expected, nightRunning: false))
    }

    /// Только лог в /tmp при правильных слотах — тоже повод переписать.
    func testTmpLogAloneNeedsMigration() {
        var cur = expected
        cur["StandardOutPath"] = "/tmp/nightly.log"
        XCTAssertTrue(NightlyAgent.needsMigration(installed: cur, expected: expected, nightRunning: false))
    }

    /// Корень с пробелом (`Application Support`) — свой агент, миграция идёт.
    /// Разбиение аргументов на слова в круге 3 резало путь, и миграция молчала.
    func testRootWithSpaceMigrates() throws {
        let (root0, agents) = try fixture()
        let root = root0.deletingLastPathComponent().appendingPathComponent("My Root")
        try FileManager.default.moveItem(at: root0, to: root)
        let script = root.path + "/scripts/nightly.sh"
        let target = NightlyAgent.plistURL(in: agents)
        try write(oldJob(script: script), to: target)
        let want = NightlyAgent.job(script: script, logPath: root.path + "/logs/nightly.log")
        XCTAssertTrue(NightlyAgent.needsMigration(installed: try read(target), expected: want, nightRunning: false))
        XCTAssertEqual(NightlyAgent.install(root: root, agentsDir: agents) { _ in }, .done)
        XCTAssertEqual((try read(target))["ProgramArguments"] as? [String], ["/bin/bash", script])
    }

    /// Чужой `Program` при нашей команде снимается: launchd исполнял бы его.
    func testForeignProgramKeyDropped() {
        var old = oldJob(script: "/other/scripts/nightly.sh")
        old["Program"] = "/other/scripts/nightly.sh"
        let out = NightlyAgent.reconciled(installed: old, expected: expected)
        XCTAssertNil(out["Program"])
        XCTAssertEqual(out["ProgramArguments"] as? [String], ["/bin/bash", "/x/scripts/nightly.sh"])
    }

    /// Чужой `Program` рядом с нашим скриптом в argv: при явной установке
    /// команда наша целиком (круг 5).
    func testProgramBesideOwnScriptDroppedOnInstall() {
        var old = oldJob()
        old["Program"] = "/other/scripts/nightly.sh"
        let out = NightlyAgent.reconciled(installed: old, expected: expected)
        XCTAssertNil(out["Program"])
        XCTAssertEqual(out["ProgramArguments"] as? [String], ["/bin/bash", "/x/scripts/nightly.sh"])
    }

    /// Ручной plist с `Program` молча не мигрирует: ни приложение, ни SETUP
    /// такой не пишут, а `Program` с голым argv[0] — законная обёртка.
    func testProgramKeyBlocksAutoMigration() {
        var old = oldJob()
        old["Program"] = "/usr/bin/caffeinate"
        XCTAssertFalse(NightlyAgent.needsMigration(installed: old, expected: expected, nightRunning: false))
    }

    /// Приведённый plist больше не мигрирует: сравнение — весь словарь.
    func testReconciledNotMigratedAgain() {
        var old = oldJob()
        old["EnvironmentVariables"] = ["CHAROITE_ROOT": "/x", "NIGHTLY_MAX_H": 3]
        old["KeepAlive"] = false
        let once = NightlyAgent.reconciled(installed: old, expected: expected)
        XCTAssertFalse(NightlyAgent.needsMigration(installed: once, expected: expected, nightRunning: false))
        XCTAssertTrue(NightlyAgent.needsMigration(installed: old, expected: expected, nightRunning: false))
    }

    /// Через диск: `<integer>`, `<false/>`, вложенное окружение — приведённый
    /// plist после записи и чтения не мигрирует снова (круг 6).
    func testReconciledStableThroughDisk() throws {
        var old = oldJob()
        old["EnvironmentVariables"] = ["CHAROITE_ROOT": "/x", "NIGHTLY_MAX_H": 3]
        old["KeepAlive"] = false
        let url = try tmpDir().appendingPathComponent("a.plist")
        try write(NightlyAgent.reconciled(installed: old, expected: expected), to: url)
        XCTAssertFalse(NightlyAgent.needsMigration(installed: try read(url), expected: expected, nightRunning: false))
    }

    /// Подпись Настроек — по файлу агента: старый 04:15 не выдаётся за новые,
    /// расписание, которое не сводится к «час:минута», и иные поводы запуска не
    /// выдумываются, чужая команда с нашими слотами — не наш агент (круги 7–8).
    func testShownReadsFile() throws {
        let dir = try tmpDir()
        let script = "/x/scripts/nightly.sh"
        func shown(_ edit: (inout [String: Any]) -> Void) throws -> NightlyAgent.Shown {
            var job = expected
            edit(&job)
            let url = dir.appendingPathComponent(UUID().uuidString + ".plist")
            try write(job, to: url)
            return NightlyAgent.shown(at: url, script: script)
        }
        func cal(_ v: Any?, disabled: Bool = false) -> (inout [String: Any]) -> Void {
            { $0["StartCalendarInterval"] = v; if disabled { $0["Disabled"] = true } }
        }
        XCTAssertEqual(try shown { _ in }, .ours)
        XCTAssertEqual(try shown(cal([["Hour": 4, "Minute": 15], ["Hour": 21, "Minute": 0],
                                      ["Hour": 20, "Minute": 0], ["Hour": 19, "Minute": 0]])), .ours,
                       "другой порядок тех же слотов")
        XCTAssertEqual(try shown(cal(["Hour": 4, "Minute": 15])), .stale("04:15"), "прежняя версия")
        XCTAssertEqual(try shown { $0["EnvironmentVariables"] = [String: Any]() }, .stale(NightlyAgent.slotsText),
                       "без CHAROITE_SLOT")
        XCTAssertEqual(try shown(cal(["Hour": 2, "Minute": 30])), .manual("02:30"),
                       "свой час по SETUP — не прежняя версия, не мигрирует")
        XCTAssertEqual(try shown { $0["ProgramArguments"] = ["/bin/sh", "-c", "/bin/bash \(script)"] },
                       .manual(NightlyAgent.slotsText), "скрипт внутри строки sh -c")
        XCTAssertEqual(try shown { $0["ProgramArguments"] = ["/usr/bin/open", script, "-g"] },
                       .manual(NightlyAgent.slotsText), "скрипт не последний аргумент")
        for (why, args) in [("echo", ["/bin/echo", script]), ("bash -n", ["/bin/bash", "-n", script]),
                            ("обёртка caffeinate", ["/usr/bin/caffeinate", "-i", "/bin/bash", script]),
                            ("caffeinate -t", ["/usr/bin/caffeinate", "-t", script]),
                            ("скрипт сам", [script]), ("sh", ["/bin/sh", script]),
                            ("относительный путь", ["/bin/bash", "scripts/nightly.sh"]),
                            ("sh -c с аргументом", ["/bin/sh", "-c", "echo hi", script]),
                            ("чужая обёртка", ["/usr/bin/logger", "/bin/bash", script])] {
            XCTAssertEqual(try shown { $0["ProgramArguments"] = args }, .manual(NightlyAgent.slotsText), why)
        }
        XCTAssertEqual(try shown { $0["Sockets"] = [String: Any]() }, .ours, "пустой Sockets — не повод")
        XCTAssertEqual(try shown { $0["Disabled"] = true }, .disabled)
        XCTAssertEqual(try shown(cal(["Hour": 4, "Minute": 15], disabled: true)), .disabled, "выключенный прежний")
        XCTAssertEqual(try shown { $0["Disabled"] = true; $0["ProgramArguments"] = ["/bin/echo", script] },
                       .foreignDisabled, "выключенный чужой — выключен, «Включить» его не перепишет")
        XCTAssertEqual(try shown { $0["Disabled"] = true; $0["RunAtLoad"] = true }, .disabled,
                       "свой выключенный с другим поводом запуска — кнопка его включает (круг 15)")
        XCTAssertEqual(try shown { $0["ProgramArguments"] = ["/bin/bash", "/other/nightly.sh"] },
                       .manual(NightlyAgent.slotsText), "чужая команда")
        XCTAssertEqual(try shown { $0["Program"] = "/bin/zsh" }, .manual(NightlyAgent.slotsText), "Program")
        XCTAssertEqual(try shown { $0["KeepAlive"] = false }, .ours, "KeepAlive false — не повод")
        for (why, edit) in [
            ("нет расписания", cal(nil)), ("пустой массив", cal([[String: Any]]())),
            ("каждый час", cal(["Minute": 15])), ("каждую минуту часа", cal(["Hour": 4])),
            ("только вторники", cal(["Hour": 19, "Minute": 0, "Weekday": 2])),
            ("вне диапазона", cal(["Hour": 25, "Minute": 0])),
            ("дробный час", cal(["Hour": 4.5, "Minute": 0])), ("целый real", cal(["Hour": 4.0, "Minute": 0])),
            ("bool", cal(["Hour": true, "Minute": 0])), ("строка", cal(["Hour": "4", "Minute": 0])),
            ("смешанный массив", cal([["Hour": 4, "Minute": 15], "x"] as [Any])),
        ] as [(String, (inout [String: Any]) -> Void)] {
            XCTAssertEqual(try shown(edit), .irreducible, why)
        }
        for (why, edit) in [
            ("StartInterval", { $0["StartInterval"] = 3600 }), ("RunAtLoad", { $0["RunAtLoad"] = true }),
            ("KeepAlive", { $0["KeepAlive"] = true }), ("KeepAlive словарь", { $0["KeepAlive"] = ["SuccessfulExit": false] }),
            ("WatchPaths", { $0["WatchPaths"] = ["/x"] }), ("StartOnMount", { $0["StartOnMount"] = true }),
            ("Sockets", { $0["Sockets"] = ["s": ["SockServiceName": "9999"]] }),
            ("MachServices", { $0["MachServices"] = ["x.y": true] }),
        ] as [(String, (inout [String: Any]) -> Void)] {
            XCTAssertEqual(try shown(edit), .otherTrigger, why)
        }
        let broken = dir.appendingPathComponent("broken.plist")
        try Data("<plist><dict><key>".utf8).write(to: broken)
        XCTAssertEqual(NightlyAgent.shown(at: broken, script: script), .unreadable)
    }

    /// Миграция приводит слоты и не снимает чужие поводы запуска: `RunAtLoad`
    /// человека остаётся, подпись честно говорит «не только по расписанию» (круг 9).
    /// Миграция — только у агента, которого подпись называет нашим: ручное
    /// расписание и другие поводы запуска приводит человек (круг 10).
    func testMigrationOnlyForOursOrStale() {
        for (why, edit) in [
            ("RunAtLoad", { $0["RunAtLoad"] = true }),
            ("только вторники", { $0["StartCalendarInterval"] = ["Hour": 19, "Minute": 0, "Weekday": 2] }),
            ("каждый час", { $0["StartCalendarInterval"] = ["Minute": 15] }),
            ("пустой массив", { $0["StartCalendarInterval"] = [[String: Any]]() }),
            ("нет расписания", { $0.removeValue(forKey: "StartCalendarInterval") }),
            ("Disabled", { $0["Disabled"] = true }),
            ("echo", { $0["ProgramArguments"] = ["/bin/echo", "/x/scripts/nightly.sh"] }),
        ] as [(String, (inout [String: Any]) -> Void)] {
            var old = oldJob()
            edit(&old)
            XCTAssertFalse(NightlyAgent.needsMigration(installed: old, expected: expected, nightRunning: false), why)
        }
    }

    /// «Включить» на выключенном в файле агенте снимает `Disabled`, окружение остаётся.
    func testInstallClearsDisabledKeepsEnv() throws {
        let (root, agents) = try fixture()
        var cur = NightlyAgent.job(script: root.path + "/scripts/nightly.sh",
                                   logPath: root.path + "/logs/nightly.log")
        cur["Disabled"] = true
        cur["EnvironmentVariables"] = ["CHAROITE_SLOT": "1", "NIGHTLY_MAX_H": "3"]
        let target = NightlyAgent.plistURL(in: agents)
        try write(cur, to: target)
        XCTAssertEqual(NightlyAgent.install(root: root, agentsDir: agents) { _ in }, .done)
        let back = try read(target)
        XCTAssertNil(back["Disabled"])
        XCTAssertEqual((back["EnvironmentVariables"] as? [String: Any])?["NIGHTLY_MAX_H"] as? String, "3")
    }

    /// Скрипт внутри строки `sh -c` — не наш агент: миграция его не трогает (круг 9).
    func testShellStringNotMigrated() {
        var old = oldJob()
        old["ProgramArguments"] = ["/bin/sh", "-c", "/bin/bash /x/scripts/nightly.sh"]
        XCTAssertFalse(NightlyAgent.needsMigration(installed: old, expected: expected, nightRunning: false))
    }

    /// Метка в нашем файле разошлась с именем — повод переписать.
    func testLabelMismatchNeedsMigration() {
        var cur = expected
        cur["Label"] = "my.own.night"
        XCTAssertTrue(NightlyAgent.needsMigration(installed: cur, expected: expected, nightRunning: false))
    }

    /// Код в папке данных старше приложения (DMG поверх прежнего клона): без
    /// гейта четыре слота — четыре полных ночи. Ставится прежний 04:15, а
    /// четыре слота, уже записанные такому скрипту, сводятся обратно.
    func testScriptWithoutGateKeepsOneSlot() throws {
        let (root, agents) = try fixture(script: Data("echo night\n".utf8))
        let script = root.path + "/scripts/nightly.sh"
        let target = NightlyAgent.plistURL(in: agents)
        try write(NightlyAgent.job(script: script, logPath: "/l"), to: target)
        XCTAssertEqual(NightlyAgent.shown(at: target, script: script), .ours)
        var four = oldJob(script: script)
        four["StartCalendarInterval"] = NightlyAgent.slots.map { ["Hour": $0.hour, "Minute": $0.minute] }
        four["EnvironmentVariables"] = ["CHAROITE_SLOT": "1"]
        try write(four, to: target)
        XCTAssertEqual(NightlyAgent.shown(at: target, script: script), .stale(NightlyAgent.slotsText))
        XCTAssertEqual(NightlyAgent.install(root: root, agentsDir: agents) { _ in }, .done)
        XCTAssertEqual((try read(target)["StartCalendarInterval"] as? [Any])?.count, 1)
    }

    /// Гейт — два файла: строка в скрипте без night_gate.py (сбой гейта — go)
    /// и скрипт не в UTF-8 (bash его исполнит) не дают четырёх слотов (круг 14).
    func testGateNeedsBothFilesReadAsBytes() throws {
        let (root, _) = try fixture(gate: false)
        XCTAssertFalse(NightlyAgent.knowsSlots(root.path + "/scripts/nightly.sh"), "нет night_gate.py")
        var plain = Data("# caf".utf8); plain.append(0xE9); plain.append(contentsOf: Data("\necho night\n".utf8))
        let (root2, _) = try fixture(script: plain)
        XCTAssertFalse(NightlyAgent.knowsSlots(root2.path + "/scripts/nightly.sh"), "latin-1 без флага")
        var latin = Data("# caf".utf8); latin.append(0xE9); latin.append(contentsOf: Data("\n[ \"${CHAROITE_SLOT:-}\" = 1 ] && python3 scripts/night_gate.py\n".utf8))
        let (root3, _) = try fixture(script: latin)
        XCTAssertTrue(NightlyAgent.knowsSlots(root3.path + "/scripts/nightly.sh"), "latin-1 с флагом — по байтам")
    }

    /// Флаг без вызова гейта и пустой гейт — не гейт: оба дают «go» в каждый слот (круг 15).
    func testGateMustBeCalledAndNotEmpty() throws {
        let (root, _) = try fixture(script: Data("[ \"${CHAROITE_SLOT:-}\" = 1 ] && echo slot\n".utf8))
        XCTAssertFalse(NightlyAgent.knowsSlots(root.path + "/scripts/nightly.sh"), "флаг без вызова гейта")
        let (root2, _) = try fixture()
        FileManager.default.createFile(atPath: root2.path + "/scripts/night_gate.py", contents: Data())
        XCTAssertFalse(NightlyAgent.knowsSlots(root2.path + "/scripts/nightly.sh"), "пустой гейт")
        XCTAssertTrue(NightlyAgent.knowsSlots(try fixture().root.path + "/scripts/nightly.sh"))
    }

    /// Свой выключенный агент с RunAtLoad — тоже свой: кнопка его включает (круг 15).
    func testOwnDisabledOtherTriggerIsOurs() {
        var mine = oldJob()
        mine["RunAtLoad"] = true
        mine["Disabled"] = true
        XCTAssertEqual(NightlyAgent.classify(mine, script: "/x/scripts/nightly.sh"), .disabled)
    }

    /// Свой скрипт с ручным часом и Disabled включается кнопкой, а не только удаляется.
    func testOwnScriptManualHourDisabledIsOurs() {
        var mine = oldJob()
        mine["StartCalendarInterval"] = ["Hour": 2, "Minute": 30]
        mine["Disabled"] = true
        XCTAssertEqual(NightlyAgent.classify(mine, script: "/x/scripts/nightly.sh"), .disabled)
    }

    /// Свой час по SETUP переживает старт приложения.
    func testManualHourIsNotMigrated() {
        var mine = oldJob()
        mine["StartCalendarInterval"] = ["Hour": 2, "Minute": 30]
        XCTAssertFalse(NightlyAgent.needsMigration(installed: mine, expected: expected, nightRunning: false))
    }

    private func tmpDir() throws -> URL {
        let d = FileManager.default.temporaryDirectory.appendingPathComponent("night-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: d) }
        return d
    }

    /// Скрипт этой версии: знает гейт слотов. Без строки — код старше приложения.
    private let gatedScript = Data("if [ \"${CHAROITE_SLOT:-}\" = 1 ]; then python3 scripts/night_gate.py; fi\n".utf8)
    private let gateBody = Data("print('go')\n".utf8)

    private func fixture(script: Data? = nil, gate: Bool = true) throws -> (root: URL, agents: URL) {
        let d = try tmpDir()
        let root = d.appendingPathComponent("root")
        try FileManager.default.createDirectory(at: root.appendingPathComponent("scripts"),
                                                withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: root.path + "/scripts/nightly.sh", contents: script ?? gatedScript)
        if gate { FileManager.default.createFile(atPath: root.path + "/scripts/night_gate.py", contents: gateBody) }
        let agents = d.appendingPathComponent("LaunchAgents")
        try FileManager.default.createDirectory(at: agents, withIntermediateDirectories: true)
        return (root, agents)
    }

    private func write(_ job: [String: Any], to url: URL) throws {
        try PropertyListSerialization.data(fromPropertyList: job, format: .xml, options: 0).write(to: url)
    }

    private func read(_ url: URL) throws -> [String: Any] {
        try XCTUnwrap(PropertyListSerialization.propertyList(from: Data(contentsOf: url), format: nil)
                      as? [String: Any])
    }
}
