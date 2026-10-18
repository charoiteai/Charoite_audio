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
        FileManager.default.createFile(atPath: real.path + "/scripts/nightly.sh", contents: Data())
        let link = dir.appendingPathComponent("link")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: real)
        let want = NightlyAgent.job(script: real.path + "/scripts/nightly.sh", logPath: "/l")
        XCTAssertTrue(NightlyAgent.needsMigration(installed: oldJob(script: link.path + "/scripts/nightly.sh"),
                                                  expected: want, nightRunning: false))
    }

    /// Другая метка уже ставит ночь этого корня — второй агент не пишем.
    func testOtherLabelBlocksInstall() throws {
        let (root, agents) = try fixture()
        var other = oldJob(script: root.path + "/scripts/nightly.sh")
        other["Label"] = "my.own.night"
        try write(other, to: agents.appendingPathComponent("my.own.night.plist"))
        var calls: [[String]] = []
        let r = NightlyAgent.install(root: root, agentsDir: agents) { calls.append($0) }
        XCTAssertEqual(r, .otherAgent("my.own.night.plist"))
        XCTAssertTrue(calls.isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: NightlyAgent.plistURL(in: agents).path))
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

    // ─ выходной круг 3 №630: типы окружения, обёртка, лог, один обход каталога ─

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

    /// Обёртка `caffeinate` вокруг скрипта этой установки переживает миграцию,
    /// а лог из общего /tmp переезжает к данным.
    func testWrapperKeptTmpLogMoved() throws {
        let (root, agents) = try fixture()
        let script = root.path + "/scripts/nightly.sh"
        var old = oldJob(script: script)
        let wrapped = ["/usr/bin/caffeinate", "-i", "/bin/bash", script]
        old["ProgramArguments"] = wrapped
        old["StandardOutPath"] = "/tmp/nightly.log"
        let target = NightlyAgent.plistURL(in: agents)
        try write(old, to: target)
        XCTAssertEqual(NightlyAgent.install(root: root, agentsDir: agents) { _ in }, .done)
        let back = try read(target)
        XCTAssertEqual(back["ProgramArguments"] as? [String], wrapped)
        XCTAssertEqual(back["StandardOutPath"] as? String, root.path + "/logs/nightly.log")
        XCTAssertEqual((back["StartCalendarInterval"] as? [Any])?.count, 4)
    }

    /// Только лог в /tmp при правильных слотах — тоже повод переписать.
    func testTmpLogAloneNeedsMigration() {
        var cur = expected
        cur["StandardOutPath"] = "/tmp/nightly.log"
        XCTAssertTrue(NightlyAgent.needsMigration(installed: cur, expected: expected, nightRunning: false))
    }

    /// Свой агент уже стоит — вторая метка обновлению не мешает (круг 3: отказ
    /// держал старые слоты вечно, а настройки показывали «включён»).
    func testOtherLabelDoesNotBlockUpdate() throws {
        let (root, agents) = try fixture()
        let script = root.path + "/scripts/nightly.sh"
        var other = oldJob(script: script)
        other["Label"] = "my.own.night"
        try write(other, to: agents.appendingPathComponent("my.own.night.plist"))
        try write(oldJob(script: script), to: NightlyAgent.plistURL(in: agents))
        XCTAssertEqual(NightlyAgent.install(root: root, agentsDir: agents) { _ in }, .done)
    }

    /// Выключенный чужой агент ночь не ставит — включению не мешает.
    func testDisabledOtherAgentIgnored() throws {
        let (root, agents) = try fixture()
        var other = oldJob(script: root.path + "/scripts/nightly.sh")
        other["Label"] = "my.own.night"
        other["Disabled"] = true
        try write(other, to: agents.appendingPathComponent("my.own.night.plist"))
        XCTAssertEqual(NightlyAgent.install(root: root, agentsDir: agents) { _ in }, .done)
    }

    /// Скрипт словом внутри строки `sh -c` — тоже вторая ночь.
    func testShellWrapperIsOtherAgent() throws {
        let (root, agents) = try fixture()
        let job: [String: Any] = ["Label": "wrap",
                                  "ProgramArguments": ["/bin/sh", "-c", "bash \(root.path)/scripts/nightly.sh --fast"]]
        try write(job, to: agents.appendingPathComponent("wrap.plist"))
        XCTAssertEqual(NightlyAgent.install(root: root, agentsDir: agents) { _ in }, .otherAgent("wrap.plist"))
    }

    /// Бинарный plist статус «Сегодня» видит так же, как установка.
    func testBinaryPlistSeenByStatus() throws {
        let (root, agents) = try fixture()
        let script = root.path + "/scripts/nightly.sh"
        try PropertyListSerialization.data(fromPropertyList: oldJob(script: script), format: .binary, options: 0)
            .write(to: agents.appendingPathComponent("my.own.night.plist"))
        XCTAssertEqual(NightlyStatus.agentScriptPath(inAgentsAt: agents), script)
        XCTAssertEqual(NightlyAgent.install(root: root, agentsDir: agents) { _ in }, .otherAgent("my.own.night.plist"))
    }

    private func tmpDir() throws -> URL {
        let d = FileManager.default.temporaryDirectory.appendingPathComponent("night-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: d) }
        return d
    }

    private func fixture() throws -> (root: URL, agents: URL) {
        let d = try tmpDir()
        let root = d.appendingPathComponent("root")
        try FileManager.default.createDirectory(at: root.appendingPathComponent("scripts"),
                                                withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: root.path + "/scripts/nightly.sh", contents: Data())
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
