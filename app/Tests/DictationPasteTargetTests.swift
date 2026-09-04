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
        XCTAssertEqual(DictationService.draftAction(security: .clear, captured: 5, pending: 5, secureSeen: false, trusted: true, unknownStreak: 0), .show)
        XCTAssertEqual(DictationService.draftAction(security: .clear, captured: 5, pending: 7, secureSeen: false, trusted: true, unknownStreak: 0), .showAndReread, "за полёт пришёл новый — показать старый, перечитать для нового")
        XCTAssertEqual(DictationService.draftAction(security: .clear, captured: 5, pending: nil, secureSeen: false, trusted: true, unknownStreak: 0), .show, "кусок потреблён до возврата — показать, не перечитывать (GLM r16 M2)")
        XCTAssertEqual(DictationService.draftAction(security: .clear, captured: nil, pending: 7, secureSeen: false, trusted: true, unknownStreak: 0), .reread, "сторож читал без куска, кусок пришёл за полёт — перечитать")
        XCTAssertEqual(DictationService.draftAction(security: .clear, captured: nil, pending: nil, secureSeen: false, trusted: true, unknownStreak: 0), .wait)
        XCTAssertEqual(DictationService.draftAction(security: .secure, captured: 5, pending: 7, secureSeen: false, trusted: true, unknownStreak: 0), .latch)
        XCTAssertEqual(DictationService.draftAction(security: .unknown, captured: 5, pending: 5, secureSeen: false, trusted: true, unknownStreak: 0), .hideAndReread, "не ответило, кусок ждёт — плашка гаснет и цепное перечитывание")
        XCTAssertEqual(DictationService.draftAction(security: .unknown, captured: 5, pending: 5, secureSeen: false, trusted: true, unknownStreak: 1), .hideAndReread, "второе подряд — ещё одно перечитывание")
        XCTAssertEqual(DictationService.draftAction(security: .unknown, captured: 5, pending: 5, secureSeen: false, trusted: true, unknownStreak: 2), .hide, "два подряд — дальше ждём сторожа")
        XCTAssertEqual(DictationService.draftAction(security: .unknown, captured: nil, pending: nil, secureSeen: false, trusted: true, unknownStreak: 0), .hide, "не ответило, куска нет — только гаснет")
        XCTAssertEqual(DictationService.draftAction(security: .clear, captured: 5, pending: 7, secureSeen: true, trusted: true, unknownStreak: 0), .hide, "защёлка важнее перечитывания (GLM r13 M1)")
        XCTAssertEqual(DictationService.draftAction(security: .unknown, captured: 5, pending: 7, secureSeen: true, trusted: true, unknownStreak: 0), .hide, "защёлка важнее цепного перечитывания")
        XCTAssertEqual(DictationService.draftAction(security: .clear, captured: 5, pending: 5, secureSeen: false, trusted: false, unknownStreak: 0), .hide, "без права AX плашки нет")
    }

    func testDraftOutcomeShowsTheCapturedPieceAndKeepsANewerPending() {
        let old = (text: "раз", serial: 5), new = (text: "раз два", serial: 7)
        let shown = DictationService.draftOutcome(action: .show, captured: old, pending: old)
        XCTAssertEqual(shown, DictationService.DraftOutcome(shown: "раз", clearPending: true, reread: false, hide: false, latch: false))
        let chained = DictationService.draftOutcome(action: .showAndReread, captured: old, pending: new)
        XCTAssertEqual(chained, DictationService.DraftOutcome(shown: "раз", clearPending: false, reread: true, hide: false, latch: false),
                       "новый кусок остаётся ждать своё чтение, показан захваченный")
        XCTAssertEqual(DictationService.draftOutcome(action: .latch, captured: old, pending: new),
                       DictationService.DraftOutcome(shown: nil, clearPending: true, reread: false, hide: true, latch: true))
        XCTAssertEqual(DictationService.draftOutcome(action: .hideAndReread, captured: old, pending: new),
                       DictationService.DraftOutcome(shown: nil, clearPending: false, reread: true, hide: true, latch: false),
                       "кусок не выбрасывается")
        XCTAssertEqual(DictationService.draftOutcome(action: .reread, captured: nil, pending: new),
                       DictationService.DraftOutcome(shown: nil, clearPending: false, reread: true, hide: false, latch: false))
        XCTAssertEqual(DictationService.draftOutcome(action: .wait, captured: nil, pending: nil), DictationService.DraftOutcome(shown: nil))
        XCTAssertEqual(DictationService.draftOutcome(action: .hide, captured: old, pending: new),
                       DictationService.DraftOutcome(shown: nil, hide: true), "плашка гаснет, кусок остаётся ждать")
    }

    func testUnknownStreakCountsOnlyWhileAPieceWaits() {
        XCTAssertEqual(DictationService.nextUnknownStreak(security: .unknown, pieceWaiting: false, streak: 0), 0, "пустые чтения сторожа бюджет не тратят")
        XCTAssertEqual(DictationService.nextUnknownStreak(security: .unknown, pieceWaiting: true, streak: 0), 1)
        XCTAssertEqual(DictationService.nextUnknownStreak(security: .unknown, pieceWaiting: true, streak: 1), 2)
        XCTAssertEqual(DictationService.nextUnknownStreak(security: .clear, pieceWaiting: true, streak: 2), 0, "ответил — счёт с нуля")
        XCTAssertEqual(DictationService.nextUnknownStreak(security: .unknown, pieceWaiting: false, streak: 2), 0, "куска нет — эпизод кончился")
    }

    func testBlindPasteKeyPrefersTheBundleIdentifier() {
        XCTAssertEqual(DictationService.blindPasteKey(bundleID: "com.x.app", pid: 42), "com.x.app")
        XCTAssertEqual(DictationService.blindPasteKey(bundleID: nil, pid: 42), "42")
    }

    func testUnknownFlashOnlyWithTheAccessibilityRight() {
        XCTAssertTrue(DictationService.unknownFlashAllowed(trusted: true, security: .unknown, secureSeen: false))
        XCTAssertFalse(DictationService.unknownFlashAllowed(trusted: false, security: .unknown, secureSeen: false), "без права вставки нет — сообщение про неё соврало бы")
        XCTAssertFalse(DictationService.unknownFlashAllowed(trusted: true, security: .clear, secureSeen: false))
        XCTAssertFalse(DictationService.unknownFlashAllowed(trusted: true, security: .unknown, secureSeen: true))
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
