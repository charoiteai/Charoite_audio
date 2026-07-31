import XCTest
@testable import CharoiteApp

final class MeetingNotificationTests: XCTestCase {
    func testSameMeetingIsClaimedOnlyOnce() {
        var ledger = MeetingNotificationLedger()

        XCTAssertTrue(ledger.claim("standup"))
        XCTAssertFalse(ledger.claim("standup"),
                       "минутный таймер не должен повторять одно уведомление")
    }

    func testOverlappingMeetingsDoNotBringTheFirstOneBack() {
        var ledger = MeetingNotificationLedger()

        XCTAssertTrue(ledger.claim("first"))
        XCTAssertTrue(ledger.claim("second"))
        XCTAssertFalse(ledger.claim("first"),
                       "первая из пересекающихся встреч уже была показана")
    }

    func testResetAllowsNotificationAfterCalendarIsEnabledAgain() {
        var ledger = MeetingNotificationLedger()
        XCTAssertTrue(ledger.claim("standup"))

        ledger.reset()

        XCTAssertTrue(ledger.claim("standup"))
    }
}
