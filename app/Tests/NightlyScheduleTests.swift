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
}
