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
        XCTAssertTrue(DictationService.liveStripAllowed(security: .clear, secureSeen: false, trusted: true))
        XCTAssertFalse(DictationService.liveStripAllowed(security: .secure, secureSeen: false, trusted: true))
        XCTAssertFalse(DictationService.liveStripAllowed(security: .unknown, secureSeen: false, trusted: true), "приложение не ответило — что под фокусом, неизвестно, плашки нет")
        XCTAssertFalse(DictationService.liveStripAllowed(security: .clear, secureSeen: true, trusted: true), "побывали в пароле — текст не возвращается")
        XCTAssertFalse(DictationService.liveStripAllowed(security: .clear, secureSeen: false, trusted: false), "без права AX пароль не отличить — плашки нет")
        XCTAssertFalse(DictationService.liveStripAllowed(security: .secure, secureSeen: true, trusted: true))
    }

    func testDraftActionShowsTheCapturedPieceAndRereadsForANewerOne() {
        XCTAssertEqual(DictationService.draftAction(security: .clear, captured: 5, pending: 5, secureSeen: false, trusted: true), .show)
        XCTAssertEqual(DictationService.draftAction(security: .clear, captured: 5, pending: nil, secureSeen: false, trusted: true), .show, "кусок уже никто не ждёт — показать захваченный")
        XCTAssertEqual(DictationService.draftAction(security: .clear, captured: 5, pending: 7, secureSeen: false, trusted: true), .showAndReread, "за полёт пришёл новый — показать старый, перечитать для нового")
        XCTAssertEqual(DictationService.draftAction(security: .clear, captured: nil, pending: 7, secureSeen: false, trusted: true), .reread, "сторож читал без куска, кусок пришёл за полёт — перечитать")
        XCTAssertEqual(DictationService.draftAction(security: .clear, captured: nil, pending: nil, secureSeen: false, trusted: true), .wait)
        XCTAssertEqual(DictationService.draftAction(security: .secure, captured: 5, pending: 7, secureSeen: false, trusted: true), .latch)
        XCTAssertEqual(DictationService.draftAction(security: .unknown, captured: 5, pending: 5, secureSeen: false, trusted: true), .hide, "не ответило — плашка гаснет, кусок ждёт")
        XCTAssertEqual(DictationService.draftAction(security: .clear, captured: 5, pending: 7, secureSeen: true, trusted: true), .hide, "защёлка важнее перечитывания (GLM r13 M1)")
        XCTAssertEqual(DictationService.draftAction(security: .clear, captured: 5, pending: 5, secureSeen: false, trusted: false), .hide, "без права AX плашки нет")
    }

    func testFrontmostFallbackOnlyWhenTheSystemWideReadFailed() {
        XCTAssertFalse(DictationService.needsFrontmostFallback(.success))
        XCTAssertFalse(DictationService.needsFrontmostFallback(.noValue), "определённое «элемента нет» — без второго запроса")
        XCTAssertTrue(DictationService.needsFrontmostFallback(.cannotComplete))
        XCTAssertTrue(DictationService.needsFrontmostFallback(.apiDisabled))
    }

    func testFocusSecurityFromAccessibilityAnswers() {
        XCTAssertEqual(DictationService.focusSecurity(focused: .noValue, role: .failure, roleName: nil, subrole: nil), .clear,
                       "нет сфокусированного элемента — пароля нет (Chromium без accessibility)")
        XCTAssertEqual(DictationService.focusSecurity(focused: .cannotComplete, role: .failure, roleName: nil, subrole: nil), .unknown,
                       "не ответило за таймаут — зависло, плашка молчит")
        XCTAssertEqual(DictationService.focusSecurity(focused: .apiDisabled, role: .failure, roleName: nil, subrole: nil), .unknown)
        XCTAssertEqual(DictationService.focusSecurity(focused: .success, role: .cannotComplete, roleName: nil, subrole: nil), .unknown,
                       "элемент есть, роль не прочиталась — неизвестно")
        XCTAssertEqual(DictationService.focusSecurity(focused: .success, role: .success, roleName: "AXTextField", subrole: nil), .clear)
        XCTAssertEqual(DictationService.focusSecurity(focused: .success, role: .success, roleName: "AXSecureTextField", subrole: nil), .secure)
        XCTAssertEqual(DictationService.focusSecurity(focused: .success, role: .success, roleName: "AXTextField", subrole: "AXSecureTextField"), .secure)
    }

    func testSecureReadAppliesOnlyToTheLiveRecordingOfTheSameDictation() {
        XCTAssertTrue(DictationService.secureReadApplies(generation: 3, current: 3, recording: true))
        XCTAssertFalse(DictationService.secureReadApplies(generation: 3, current: 4, recording: true), "уже следующая диктовка")
        XCTAssertFalse(DictationService.secureReadApplies(generation: 3, current: 3, recording: false), "после стопа — доставка читает сама")
    }
}
