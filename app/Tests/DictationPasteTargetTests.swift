import XCTest
@testable import CharoiteApp

/// ⌘V уходит туда, где нажали ⌥⌘D, а не в окно, открытое за секунды
/// распознавания (№156, advisory DS по #486).
final class DictationPasteTargetTests: XCTestCase {
    func testSameAppPastes() {
        XCTAssertEqual(DictationService.pasteDecision(trusted: true, startedIn: 42, frontmost: 42), .paste)
    }

    func testOtherAppKeepsClipboard() {
        XCTAssertEqual(DictationService.pasteDecision(trusted: true, startedIn: 42, frontmost: 77), .windowChanged)
    }

    func testNoAccessibilityWinsOverEverything() {
        XCTAssertEqual(DictationService.pasteDecision(trusted: false, startedIn: 42, frontmost: 77), .noAccessibility)
        XCTAssertEqual(DictationService.pasteDecision(trusted: false, startedIn: 42, frontmost: 42), .noAccessibility)
    }

    func testUnknownStartOrFrontBehavesAsBefore() {
        // приложение на старте не узнали (0) или сейчас никого впереди (nil) — вставляем, как раньше
        XCTAssertEqual(DictationService.pasteDecision(trusted: true, startedIn: 0, frontmost: 77), .paste)
        XCTAssertEqual(DictationService.pasteDecision(trusted: true, startedIn: 42, frontmost: nil), .paste)
    }
}
