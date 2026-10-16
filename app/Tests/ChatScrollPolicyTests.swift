import XCTest
@testable import CharoiteApp

final class ChatScrollPolicyTests: XCTestCase {
    func testStreamingDoesNotPullReaderAwayFromHistory() {
        var policy = ChatScrollPolicy()
        XCTAssertTrue(policy.contentChanged())
        policy.beginUserScroll()
        policy.positionChanged(distanceFromBottom: 250, movedUp: true)
        policy.endUserScroll()
        for _ in 0..<100 { XCTAssertFalse(policy.contentChanged()) }
        XCTAssertTrue(policy.hasUnreadContent)
        policy.resume()
        XCTAssertTrue(policy.contentChanged())
        XCTAssertFalse(policy.hasUnreadContent)
    }

    func testWheelOrKeyboardWithoutGestureStillPauses() {
        // No live-scroll begin/end: only the clip view moved (wheel without phases, Page Up).
        var policy = ChatScrollPolicy()
        policy.positionChanged(distanceFromBottom: 400, movedUp: true)
        XCTAssertFalse(policy.contentChanged())
        XCTAssertTrue(policy.hasUnreadContent)
        policy.positionChanged(distanceFromBottom: 10, movedUp: false)
        XCTAssertTrue(policy.contentChanged())
    }

    func testOwnJumpDownNeverPausesItself() {
        // A programmatic jump can land short of the bottom while the lazy stack measures rows.
        var policy = ChatScrollPolicy()
        policy.positionChanged(distanceFromBottom: 300, movedUp: false)
        XCTAssertTrue(policy.shouldFollow)
    }

    func testGestureCancelsAlreadyScheduledJumpEvenAtBottom() {
        var policy = ChatScrollPolicy()
        XCTAssertTrue(policy.contentChanged()) // queued by the view
        policy.beginUserScroll()
        XCTAssertFalse(policy.shouldFollow) // rechecked when queue executes
        XCTAssertFalse(policy.contentChanged())
        policy.positionChanged(distanceFromBottom: 20, movedUp: true)
        policy.endUserScroll()
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

    func testManualPauseSurvivesTouchNearBottom() {
        var policy = ChatScrollPolicy()
        policy.pause()
        policy.beginUserScroll()
        policy.positionChanged(distanceFromBottom: 30, movedUp: true)
        policy.positionChanged(distanceFromBottom: 0, movedUp: false)
        policy.endUserScroll()
        XCTAssertFalse(policy.shouldFollow)
        XCTAssertFalse(policy.contentChanged())
        policy.resume()
        XCTAssertTrue(policy.shouldFollow)
    }

    func testResumeClearsStuckGesture() {
        // didEndLiveScroll can be lost when the observed scroll view is replaced mid-gesture.
        var policy = ChatScrollPolicy()
        policy.beginUserScroll()
        policy.positionChanged(distanceFromBottom: 500, movedUp: true)
        policy.resume()
        XCTAssertTrue(policy.shouldFollow)
    }

    func testBottomToleranceAndUnknownGeometry() {
        var policy = ChatScrollPolicy()
        policy.positionChanged(distanceFromBottom: 81, movedUp: true)
        XCTAssertFalse(policy.shouldFollow)
        policy.positionChanged(distanceFromBottom: 80, movedUp: false)
        XCTAssertTrue(policy.shouldFollow)
        policy.positionChanged(distanceFromBottom: .nan, movedUp: true)
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
