import XCTest
@testable import CharoiteApp

final class ChatScrollPolicyTests: XCTestCase {
    func testStreamingDoesNotPullReaderAwayFromHistory() {
        var policy = ChatScrollPolicy()
        XCTAssertTrue(policy.contentChanged())
        policy.beginUserScroll()
        policy.updateUserScroll(distanceFromBottom: 250)
        policy.endUserScroll(distanceFromBottom: 250)
        for _ in 0..<100 { XCTAssertFalse(policy.contentChanged()) }
        XCTAssertTrue(policy.hasUnreadContent)
        policy.resume()
        XCTAssertTrue(policy.contentChanged())
        XCTAssertFalse(policy.hasUnreadContent)
    }

    func testGestureCancelsAlreadyScheduledJumpEvenAtBottom() {
        var policy = ChatScrollPolicy()
        XCTAssertTrue(policy.contentChanged()) // queued by the view
        policy.beginUserScroll()
        XCTAssertFalse(policy.shouldFollow) // rechecked when queue executes
        XCTAssertFalse(policy.contentChanged())
        policy.endUserScroll(distanceFromBottom: 20)
        XCTAssertTrue(policy.shouldFollow)
        XCTAssertFalse(policy.hasUnreadContent)
    }

    func testManualPauseAndReturnWithoutNewContent() {
        var policy = ChatScrollPolicy()
        policy.pause()
        XCTAssertFalse(policy.shouldFollow)
        XCTAssertFalse(policy.hasUnreadContent)
        policy.resume()
        XCTAssertTrue(policy.shouldFollow)
    }

    func testManualReturnDuringGestureWaitsUntilGestureEnds() {
        var policy = ChatScrollPolicy()
        policy.beginUserScroll()
        policy.pause()
        policy.resume()
        XCTAssertFalse(policy.shouldFollow)
        policy.endUserScroll(distanceFromBottom: 0)
        XCTAssertTrue(policy.shouldFollow)
    }

    func testBottomToleranceAndUnknownGeometry() {
        var policy = ChatScrollPolicy()
        policy.updateUserScroll(distanceFromBottom: 81)
        XCTAssertFalse(policy.shouldFollow)
        policy.updateUserScroll(distanceFromBottom: 80)
        XCTAssertTrue(policy.shouldFollow)
        policy.updateUserScroll(distanceFromBottom: .nan)
        XCTAssertFalse(policy.shouldFollow)
    }

    func testFlippedAndUnflippedDocumentCoordinates() {
        let document = CGRect(x: 0, y: 0, width: 400, height: 1000)
        let top = CGRect(x: 0, y: 0, width: 400, height: 300)
        let bottom = CGRect(x: 0, y: 700, width: 400, height: 300)
        XCTAssertEqual(ChatScrollPolicy.distanceFromBottom(document: document, visible: top, flipped: true), 700)
        XCTAssertEqual(ChatScrollPolicy.distanceFromBottom(document: document, visible: bottom, flipped: true), 0)
        XCTAssertEqual(ChatScrollPolicy.distanceFromBottom(document: document, visible: top, flipped: false), 0)
        XCTAssertEqual(ChatScrollPolicy.distanceFromBottom(document: document, visible: bottom, flipped: false), 700)
        XCTAssertEqual(ChatScrollPolicy.distanceFromBottom(document: top, visible: document, flipped: true), 0)
    }
}
