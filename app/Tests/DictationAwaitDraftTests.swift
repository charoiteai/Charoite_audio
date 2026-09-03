import ApplicationServices
import XCTest
@testable import CharoiteApp

/// Потолок ожидания черновика и срок сторожа — ровно то, что ломалось
/// на кругах #486: TaskGroup ждал всех детей, и «8 секунд» не было.
final class DictationAwaitDraftTests: XCTestCase {
    func testDraftBeforeTimeoutWins() async {
        let finish = Task<String, Never> { "черновик" }
        let draft = await DictationService.awaitDraft(finish, timeout: .seconds(2))
        XCTAssertEqual(draft, "черновик")
    }

    func testTimeoutDoesNotWaitForFinalisation() async {
        let finish = Task<String, Never> {
            try? await Task.sleep(for: .seconds(3))
            return "поздно"
        }
        let started = ContinuousClock.now
        let draft = await DictationService.awaitDraft(finish, timeout: .milliseconds(200))
        let elapsed = ContinuousClock.now - started
        XCTAssertEqual(draft, "", "поздний черновик не должен подменять итог")
        XCTAssertLessThan(elapsed, .seconds(2), "потолок не сработал — ждали финализацию")
        finish.cancel()
    }

    func testAlreadyFinishedDraftReturnsAtOnce() async {
        let finish = Task<String, Never> { "готово" }
        _ = await finish.value
        let draft = await DictationService.awaitDraft(finish, timeout: .seconds(1))
        XCTAssertEqual(draft, "готово")
    }

    func testWatchdogGraceGrowsWithRecording() {
        XCTAssertEqual(DictationService.watchdogGrace(recorded: 0), 25)
        XCTAssertEqual(DictationService.watchdogGrace(recorded: 20), 29)
        XCTAssertEqual(DictationService.watchdogGrace(recorded: 600), 145)   // 10 минут речи
        XCTAssertEqual(DictationService.watchdogGrace(recorded: -5), 25)     // часы назад — не отрицательный срок
    }

    func testSecureFieldCheckIsFalseWithoutAccessibility() {
        // На CI права Accessibility нет — контракт: «нет права → не пароль»,
        // плашка ведёт себя как раньше, а вставки ⌘V всё равно не будет.
        if !AXIsProcessTrusted() {
            XCTAssertFalse(DictationService.focusedFieldIsSecure())
        }
    }
}
