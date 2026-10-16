import XCTest
@testable import CharoiteApp

final class ChatScrollPolicyTests: XCTestCase {
    func testStreamingDoesNotPullReaderAwayFromHistory() {
        var policy = ChatScrollPolicy()
        XCTAssertTrue(policy.contentChanged())
        policy.beginUserScroll()
        policy.endUserScroll(distanceFromBottom: 250)
        for _ in 0..<100 { XCTAssertFalse(policy.contentChanged()) }
        XCTAssertTrue(policy.hasUnreadContent)
        policy.resume()
        XCTAssertTrue(policy.contentChanged())
        XCTAssertFalse(policy.hasUnreadContent)
    }

    func testGestureCancelsAlreadyScheduledJump() {
        var policy = ChatScrollPolicy()
        XCTAssertTrue(policy.contentChanged()) // queued by the view
        policy.beginUserScroll()
        XCTAssertFalse(policy.shouldFollow) // rechecked when the queue executes
        XCTAssertFalse(policy.contentChanged())
    }

    func testGestureEndingNearBottomKeepsFollowing() {
        // Our anchor sits a padding above the true bottom, and insets shift the visible rect:
        // a few dozen points are geometry, not the reader leaving.
        var policy = ChatScrollPolicy()
        policy.beginUserScroll()
        policy.endUserScroll(distanceFromBottom: 14)
        XCTAssertTrue(policy.shouldFollow)
        XCTAssertFalse(policy.hasUnreadContent)
    }

    func testGestureBackToBottomResumesAndClearsBadge() {
        var policy = ChatScrollPolicy()
        policy.beginUserScroll()
        policy.endUserScroll(distanceFromBottom: 600)
        policy.contentChanged()
        XCTAssertTrue(policy.hasUnreadContent)
        policy.beginUserScroll()
        policy.endUserScroll(distanceFromBottom: 0)
        XCTAssertTrue(policy.shouldFollow)
        XCTAssertFalse(policy.hasUnreadContent)
    }

    func testOnlyGesturesDecide() {
        // No gesture, no decision: content growth alone never pauses or resumes.
        var policy = ChatScrollPolicy()
        for _ in 0..<10 { XCTAssertTrue(policy.contentChanged()) }
        var paused = ChatScrollPolicy()
        paused.beginUserScroll()
        paused.endUserScroll(distanceFromBottom: 300)
        for _ in 0..<10 { XCTAssertFalse(paused.contentChanged()) }
        XCTAssertFalse(paused.shouldFollow)
    }

    func testManualPauseAndReturnWithoutNewContent() {
        var policy = ChatScrollPolicy()
        policy.pause()
        XCTAssertFalse(policy.shouldFollow)
        XCTAssertFalse(policy.hasUnreadContent)
        policy.resume()
        XCTAssertTrue(policy.shouldFollow)
    }

    func testManualPauseSurvivesGestureAtBottom() {
        var policy = ChatScrollPolicy()
        policy.pause()
        policy.contentChanged()
        policy.beginUserScroll()
        policy.endUserScroll(distanceFromBottom: 0)
        XCTAssertFalse(policy.shouldFollow)
        XCTAssertFalse(policy.hasUnreadContent, "at the bottom the reader has seen the new reply")
        XCTAssertFalse(policy.contentChanged())
        policy.resume()
        XCTAssertTrue(policy.shouldFollow)
    }

    func testCancelledGestureReleasesLatchAndKeepsDecision() {
        // didEndLiveScroll is lost when the observed scroll view is replaced mid-gesture.
        var following = ChatScrollPolicy()
        following.beginUserScroll()
        following.cancelUserScroll()
        XCTAssertTrue(following.shouldFollow)
        var paused = ChatScrollPolicy()
        paused.beginUserScroll()
        paused.endUserScroll(distanceFromBottom: 400)
        paused.beginUserScroll()
        paused.cancelUserScroll()
        XCTAssertFalse(paused.shouldFollow, "a lost gesture end is not a return to the bottom")
    }

    func testResumeClearsStuckGesture() {
        var policy = ChatScrollPolicy()
        policy.beginUserScroll()
        policy.resume()
        XCTAssertTrue(policy.shouldFollow)
    }

    func testBottomToleranceAndUnknownGeometry() {
        var policy = ChatScrollPolicy()
        policy.endUserScroll(distanceFromBottom: 81)
        XCTAssertFalse(policy.shouldFollow)
        policy.endUserScroll(distanceFromBottom: 80)
        XCTAssertTrue(policy.shouldFollow)
        policy.endUserScroll(distanceFromBottom: .nan)
        XCTAssertFalse(policy.shouldFollow)
        policy.endUserScroll(distanceFromBottom: .infinity)
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
