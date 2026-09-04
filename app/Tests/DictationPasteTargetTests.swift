import ApplicationServices
import XCTest
@testable import CharoiteApp

/// ⌘V уходит туда, где нажали ⌥⌘D, а не в окно, открытое за секунды
/// распознавания (№156, advisory DS по #486; круг 1 по #488).
final class DictationPasteTargetTests: XCTestCase {
    private typealias Anchor = DictationService.PasteAnchor
    private let own: pid_t = 1

    private func anchor(_ pid: pid_t, window: pid_t? = nil) -> Anchor {
        // AXUIElement приложения — заменитель окна: CFEqual по одному pid
        // истинен, по разным — ложен; права Accessibility не нужны
        Anchor(pid: pid, name: "app\(pid)", window: window.map { AXUIElementCreateApplication($0) })
    }

    func testSameAppPastes() {
        XCTAssertEqual(DictationService.pasteDecision(trusted: true, own: own, startedIn: anchor(42), now: anchor(42)), .paste)
    }

    func testOtherAppKeepsClipboard() {
        XCTAssertEqual(DictationService.pasteDecision(trusted: true, own: own, startedIn: anchor(42), now: anchor(77)), .windowChanged)
    }

    func testOtherWindowOfSameAppKeepsClipboard() {
        XCTAssertEqual(DictationService.pasteDecision(trusted: true, own: own,
                                                      startedIn: anchor(42, window: 42), now: anchor(42, window: 43)), .windowChanged)
        XCTAssertEqual(DictationService.pasteDecision(trusted: true, own: own,
                                                      startedIn: anchor(42, window: 42), now: anchor(42, window: 42)), .paste)
    }

    func testUnknownWindowOnEitherSideFallsBackToApp() {
        XCTAssertEqual(DictationService.pasteDecision(trusted: true, own: own,
                                                      startedIn: anchor(42, window: 42), now: anchor(42)), .paste)
        XCTAssertEqual(DictationService.pasteDecision(trusted: true, own: own,
                                                      startedIn: anchor(42), now: anchor(42, window: 43)), .paste)
    }

    func testOwnAppAsStartIsNotAnAnchor() {
        // строка меню-бара / окно чата: цель ещё впереди — вставляем, как раньше
        XCTAssertEqual(DictationService.pasteDecision(trusted: true, own: own, startedIn: anchor(own), now: anchor(77)), .paste)
    }

    func testOwnAppInFrontAtDeliveryKeepsClipboard() {
        // человек открыл панель Чароита посмотреть статус — в неё не вставляем
        XCTAssertEqual(DictationService.pasteDecision(trusted: true, own: own, startedIn: anchor(42), now: anchor(own)), .windowChanged)
    }

    func testNoAccessibilityWinsOverEverything() {
        XCTAssertEqual(DictationService.pasteDecision(trusted: false, own: own, startedIn: anchor(42), now: anchor(77)), .noAccessibility)
    }

    func testUnknownStartOrFrontBehavesAsBefore() {
        XCTAssertEqual(DictationService.pasteDecision(trusted: true, own: own, startedIn: nil, now: anchor(77)), .paste)
        XCTAssertEqual(DictationService.pasteDecision(trusted: true, own: own, startedIn: anchor(42), now: nil), .paste)
    }

    func testSecretNeverPastesAnywhere() {
        // касание поля пароля — ни в обычное поле, ни в само поле пароля: статус
        // и плашка Чароита не маскируют, а ⌘V под secure input глотается (DS r7)
        XCTAssertEqual(DictationService.finalDecision(trusted: true, own: own, startedIn: anchor(42), now: anchor(42), secureSeen: true, nowSecure: false), .secret)
        XCTAssertEqual(DictationService.finalDecision(trusted: true, own: own, startedIn: anchor(42), now: anchor(77), secureSeen: true, nowSecure: true), .secret)
        XCTAssertEqual(DictationService.finalDecision(trusted: true, own: own, startedIn: nil, now: nil, secureSeen: true, nowSecure: false), .secret)
    }

    func testPasswordUnderTheDeliveryFocusIsSecretToo() {
        // диктовали в обычное поле, а к доставке кликнули в пароль (DS r8 I2)
        XCTAssertEqual(DictationService.finalDecision(trusted: true, own: own, startedIn: anchor(42), now: anchor(42), secureSeen: false, nowSecure: true), .secret)
        // старт из меню (якоря нет) и пароль под фокусом доставки — тоже .secret
        XCTAssertEqual(DictationService.finalDecision(trusted: true, own: own, startedIn: nil, now: nil, secureSeen: false, nowSecure: true), .secret)
    }

    func testNoAccessibilityStillWinsOverSecret() {
        XCTAssertEqual(DictationService.finalDecision(trusted: false, own: own, startedIn: anchor(42), now: anchor(42), secureSeen: true, nowSecure: true), .noAccessibility)
    }

    func testWithoutASecretTheWindowRuleDecides() {
        XCTAssertEqual(DictationService.finalDecision(trusted: true, own: own, startedIn: anchor(42), now: anchor(42), secureSeen: false, nowSecure: false), .paste)
        XCTAssertEqual(DictationService.finalDecision(trusted: true, own: own, startedIn: anchor(42), now: anchor(77), secureSeen: false, nowSecure: false), .windowChanged)
    }

    func testLiveStripHidesInAndAfterAPasswordFieldAndWithoutAccessibility() {
        XCTAssertTrue(DictationService.liveStripAllowed(nowSecure: false, secureSeen: false, trusted: true))
        XCTAssertFalse(DictationService.liveStripAllowed(nowSecure: true, secureSeen: false, trusted: true))
        XCTAssertFalse(DictationService.liveStripAllowed(nowSecure: false, secureSeen: true, trusted: true), "побывали в пароле — текст не возвращается")
        XCTAssertFalse(DictationService.liveStripAllowed(nowSecure: false, secureSeen: false, trusted: false), "без права AX пароль не отличить — плашки нет")
        XCTAssertFalse(DictationService.liveStripAllowed(nowSecure: true, secureSeen: true, trusted: true))
    }

    func testSecureReadAppliesOnlyToTheLiveRecordingOfTheSameDictation() {
        XCTAssertTrue(DictationService.secureReadApplies(generation: 3, current: 3, recording: true))
        XCTAssertFalse(DictationService.secureReadApplies(generation: 3, current: 4, recording: true), "уже следующая диктовка")
        XCTAssertFalse(DictationService.secureReadApplies(generation: 3, current: 3, recording: false), "после стопа — доставка читает сама")
    }
}
