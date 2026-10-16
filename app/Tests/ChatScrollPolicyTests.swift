import XCTest
@testable import CharoiteApp

final class ChatScrollPolicyTests: XCTestCase {
    func testStreamingDoesNotPullReaderAwayFromHistory() {
        var policy = ChatScrollPolicy()
        XCTAssertTrue(policy.contentChanged())
        policy.gesture(endingAt: 250)
        for _ in 0..<100 { XCTAssertFalse(policy.contentChanged()) }
        XCTAssertTrue(policy.hasUnreadContent)
        policy.resume()
        XCTAssertTrue(policy.contentChanged())
        XCTAssertFalse(policy.hasUnreadContent)
    }

    func testGestureCancelsAlreadyScheduledJump() {
        var policy = ChatScrollPolicy()
        XCTAssertTrue(policy.contentChanged()) // queued by the view
        policy.beginUserScroll(distanceFromBottom: 0)
        XCTAssertFalse(policy.shouldFollow) // rechecked when the queue executes
        XCTAssertFalse(policy.contentChanged())
    }

    func testGestureEndingNearBottomKeepsFollowing() {
        // Our anchor sits a padding above the true bottom, and insets shift the visible rect:
        // a few dozen points are geometry, not the reader leaving.
        var policy = ChatScrollPolicy()
        policy.gesture(endingAt: 14)
        XCTAssertTrue(policy.shouldFollow)
        XCTAssertFalse(policy.hasUnreadContent)
    }

    func testGestureBackToBottomResumesAndClearsBadge() {
        var policy = ChatScrollPolicy()
        policy.gesture(endingAt: 600)
        policy.contentChanged()
        XCTAssertTrue(policy.hasUnreadContent)
        policy.gesture(endingAt: 0)
        XCTAssertTrue(policy.shouldFollow)
        XCTAssertFalse(policy.hasUnreadContent)
    }

    func testOnlyGesturesDecide() {
        // No gesture, no decision: content growth alone never pauses or resumes.
        var policy = ChatScrollPolicy()
        for _ in 0..<10 { XCTAssertTrue(policy.contentChanged()) }
        var paused = ChatScrollPolicy()
        paused.gesture(endingAt: 300)
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
        policy.gesture(endingAt: 0)
        XCTAssertFalse(policy.shouldFollow)
        XCTAssertFalse(policy.hasUnreadContent, "at the bottom the reader has seen the new reply")
        XCTAssertFalse(policy.contentChanged())
        policy.resume()
        XCTAssertTrue(policy.shouldFollow)
    }

    func testCancelledGestureReleasesLatchAndKeepsDecision() {
        // didEndLiveScroll is lost when the observed scroll view is replaced mid-gesture.
        var following = ChatScrollPolicy()
        following.beginUserScroll(distanceFromBottom: 0)
        following.cancelUserScroll()
        XCTAssertTrue(following.shouldFollow)
        var paused = ChatScrollPolicy()
        paused.gesture(endingAt: 400)
        paused.beginUserScroll(distanceFromBottom: 400)
        paused.cancelUserScroll()
        XCTAssertFalse(paused.shouldFollow, "a lost gesture end is not a return to the bottom")
    }

    func testResumeClearsStuckGesture() {
        var policy = ChatScrollPolicy()
        policy.beginUserScroll(distanceFromBottom: 0)
        policy.resume()
        XCTAssertTrue(policy.shouldFollow)
    }

    func testBottomToleranceAndUnknownGeometry() {
        var policy = ChatScrollPolicy()
        policy.gesture(endingAt: 81)
        XCTAssertFalse(policy.shouldFollow)
        policy.gesture(endingAt: 80)
        XCTAssertTrue(policy.shouldFollow)
        policy.gesture(endingAt: .nan)
        XCTAssertTrue(policy.shouldFollow, "unknown geometry is no decision")
        policy.gesture(endingAt: .infinity)
        XCTAssertTrue(policy.shouldFollow)
        policy.gesture(endingAt: 300)
        policy.gesture(endingAt: .infinity)
        XCTAssertFalse(policy.shouldFollow, "unknown geometry does not resume either")
    }

    func testReplyGrowthDuringGestureIsNotTheReaderLeaving() {
        // Our jumps are suppressed while the gesture lasts, so the bottom runs away from a reader who stays.
        var policy = ChatScrollPolicy()
        policy.beginUserScroll(distanceFromBottom: 0)
        for _ in 0..<200 { XCTAssertFalse(policy.contentChanged()) }
        policy.endUserScroll()
        XCTAssertTrue(policy.shouldFollow)
        XCTAssertFalse(policy.hasUnreadContent)
    }

    func testLastOwnMovementDecides() {
        var up = ChatScrollPolicy()
        up.beginUserScroll(distanceFromBottom: 0)
        up.userScrolled(distanceFromBottom: 120)
        up.userScrolled(distanceFromBottom: 300)
        up.contentChanged()
        up.endUserScroll()
        XCTAssertFalse(up.shouldFollow)
        XCTAssertTrue(up.hasUnreadContent)
        var back = up
        back.beginUserScroll(distanceFromBottom: 300)
        back.userScrolled(distanceFromBottom: 10)
        back.contentChanged()
        back.endUserScroll()
        XCTAssertTrue(back.shouldFollow)
        XCTAssertFalse(back.hasUnreadContent)
    }

    func testMovementOutsideGestureIsIgnored() {
        var policy = ChatScrollPolicy()
        policy.userScrolled(distanceFromBottom: 500)
        policy.endUserScroll()
        XCTAssertTrue(policy.shouldFollow, "an end without a start is no decision")
    }

    func testReaderAtBottomWithoutGestureResumesOnly() {
        // Wheel without phases, keys, window resize: no gesture, the position may only resume.
        var policy = ChatScrollPolicy()
        policy.readerSeen(distanceFromBottom: 900)
        XCTAssertTrue(policy.shouldFollow, "a far position never pauses")
        policy.gesture(endingAt: 300)
        policy.contentChanged()
        policy.readerSeen(distanceFromBottom: 200)
        XCTAssertFalse(policy.shouldFollow)
        policy.readerSeen(distanceFromBottom: .infinity)
        XCTAssertFalse(policy.shouldFollow)
        policy.readerSeen(distanceFromBottom: 30)
        XCTAssertTrue(policy.shouldFollow)
        XCTAssertFalse(policy.hasUnreadContent)
    }

    func testPositionWithoutGestureRespectsButtonAndGesture() {
        var paused = ChatScrollPolicy()
        paused.pause()
        paused.readerSeen(distanceFromBottom: 0)
        XCTAssertFalse(paused.shouldFollow, "only the return button lifts the pause button")
        var scrolling = ChatScrollPolicy()
        scrolling.gesture(endingAt: 300)
        scrolling.beginUserScroll(distanceFromBottom: 300)
        scrolling.readerSeen(distanceFromBottom: 0)
        XCTAssertFalse(scrolling.followsLatest, "the gesture end decides, not a position seen mid-gesture")
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

private extension ChatScrollPolicy {
    /// A gesture without intermediate movement events: began and ended at the same distance.
    mutating func gesture(endingAt distance: CGFloat) {
        beginUserScroll(distanceFromBottom: distance)
        endUserScroll()
    }
}
