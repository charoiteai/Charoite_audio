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

    func testBannerIsSkippedWhenTheStripeIsAlreadyOnScreen() {
        // Полоса внутри окна и баннер — одно и то же сообщение. Пока человек
        // смотрит в открытое окно Charoite, второй экземпляр только мешает.
        XCTAssertFalse(MeetingNotificationPolicy.shouldPresent(appActive: true,
                                                              mainWindowVisible: true))
    }

    func testBannerAppearsWhenTheWindowIsNotInFront() {
        XCTAssertTrue(MeetingNotificationPolicy.shouldPresent(appActive: false,
                                                             mainWindowVisible: true),
                      "свёрнутое окно человек не видит")
        XCTAssertTrue(MeetingNotificationPolicy.shouldPresent(appActive: true,
                                                             mainWindowVisible: false),
                      "запущено из меню-бара, окна нет — напоминание нужно")
        XCTAssertTrue(MeetingNotificationPolicy.shouldPresent(appActive: false,
                                                             mainWindowVisible: false))
    }
}
