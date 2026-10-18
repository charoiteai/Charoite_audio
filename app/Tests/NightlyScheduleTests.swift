import XCTest
@testable import CharoiteApp

/// Ночь у пользователя ставит приложение: четыре слота и CHAROITE_SLOT=1, по
/// которому nightly.sh зовёт гейт. Без переменной гейт молчит, и каждый слот
/// переделывал бы уже сделанную ночь.
final class NightlyScheduleTests: XCTestCase {
    func testJobHasFourSlotsAndTheGateFlag() throws {
        let job = SettingsView.nightlyJob(script: "/x/scripts/nightly.sh", logPath: "/x/logs/nightly.log")
        let slots = try XCTUnwrap(job["StartCalendarInterval"] as? [[String: Int]])
        XCTAssertEqual(slots.map { [$0["Hour"]!, $0["Minute"]!] },
                       [[19, 0], [20, 0], [21, 0], [4, 15]])
        XCTAssertEqual(job["EnvironmentVariables"] as? [String: String], ["CHAROITE_SLOT": "1"])
        XCTAssertEqual(job["Label"] as? String, "ai.charoite.nightly")
        XCTAssertEqual(job["ProgramArguments"] as? [String], ["/bin/bash", "/x/scripts/nightly.sh"])
    }

    /// launchd принимает только то, что сериализуется в plist.
    func testJobSerializesToPlist() throws {
        let job = SettingsView.nightlyJob(script: "/a & b/nightly.sh", logPath: "/a/l.log")
        let data = try PropertyListSerialization.data(fromPropertyList: job, format: .xml, options: 0)
        let back = try XCTUnwrap(PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any])
        XCTAssertEqual((back["StartCalendarInterval"] as? [Any])?.count, 4)
        XCTAssertEqual(back["ProgramArguments"] as? [String], ["/bin/bash", "/a & b/nightly.sh"])
    }
}
