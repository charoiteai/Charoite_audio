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

    func testStripNeverShowsPasswordText() {
        XCTAssertTrue(DictationService.stripAllowed(startedSecure: false, nowSecure: false))
        XCTAssertFalse(DictationService.stripAllowed(startedSecure: true, nowSecure: false), "начали в пароле — текст не показываем")
        XCTAssertFalse(DictationService.stripAllowed(startedSecure: false, nowSecure: true), "стоим в пароле — тоже")
    }
}
