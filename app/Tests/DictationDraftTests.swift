import XCTest
@testable import CharoiteApp

/// Сборка живого черновика: подтверждённые куски копятся, плавающий хвост
/// заменяется, а не дописывается — иначе на экране одна и та же фраза
/// повторялась бы на каждый промежуточный результат движка.
final class DictationDraftTests: XCTestCase {
    func testVolatileIsReplacedNotAppended() {
        var d = DictationDraft()
        d.apply("прив", isFinal: false)
        d.apply("привет", isFinal: false)
        d.apply("привет мир", isFinal: false)
        XCTAssertEqual(d.text, "привет мир")
    }

    func testFinalClosesVolatileAndKeepsOrder() {
        var d = DictationDraft()
        d.apply("привет ми", isFinal: false)
        d.apply("привет мир", isFinal: true)
        d.apply("как де", isFinal: false)
        XCTAssertEqual(d.text, "привет мир как де")
        d.apply("как дела", isFinal: true)
        XCTAssertEqual(d.text, "привет мир как дела")
    }

    func testEmptyPiecesDoNotLeaveGaps() {
        var d = DictationDraft()
        d.apply("  ", isFinal: true)
        d.apply("", isFinal: false)
        XCTAssertEqual(d.text, "")
        d.apply(" слово ", isFinal: true)
        d.apply("   ", isFinal: false)
        XCTAssertEqual(d.text, "слово")
    }
}
