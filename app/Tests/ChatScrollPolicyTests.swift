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
        policy.beginUserScroll()
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
        following.beginUserScroll()
        following.cancelUserScroll()
        XCTAssertTrue(following.shouldFollow)
        var paused = ChatScrollPolicy()
        paused.gesture(endingAt: 400)
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
        policy.gesture(endingAt: 81)
        XCTAssertFalse(policy.shouldFollow)
        policy.gesture(endingAt: 80)
        XCTAssertTrue(policy.shouldFollow)
        policy.beginUserScroll()
        policy.endUserScroll(start: nil, lastMove: nil)
        XCTAssertTrue(policy.shouldFollow, "unknown geometry is no decision")
        policy.gesture(endingAt: 300)
        policy.beginUserScroll()
        policy.endUserScroll(start: nil, lastMove: sample(0, height: 1000))
        XCTAssertFalse(policy.shouldFollow, "unknown geometry does not resume either")
        XCTAssertFalse(policy.isUserScrolling)
    }

    func testReplyGrowthDuringGestureIsNotTheReaderLeaving() {
        // Our jumps are suppressed while the gesture lasts, so the bottom runs away from a reader who stays.
        var policy = ChatScrollPolicy()
        policy.beginUserScroll()
        for _ in 0..<200 { XCTAssertFalse(policy.contentChanged()) }
        policy.endUserScroll(start: sample(0, height: 1000), lastMove: nil)
        XCTAssertTrue(policy.shouldFollow)
        XCTAssertFalse(policy.hasUnreadContent)
    }

    func testGrowthBeforeTheLastMovementIsNotTheReaders() {
        // DeepSeek, round 5: the reply grew 400 pt mid-gesture, then the reader nudged down by 10.
        // The raw distance is 390, the reader's own is -10: still at the bottom.
        var policy = ChatScrollPolicy()
        policy.beginUserScroll()
        policy.contentChanged()
        policy.endUserScroll(start: sample(0, height: 1000), lastMove: sample(390, height: 1400))
        XCTAssertTrue(policy.shouldFollow)
        XCTAssertFalse(policy.hasUnreadContent)
    }

    func testLastOwnMovementDecides() {
        var up = ChatScrollPolicy()
        up.beginUserScroll()
        up.contentChanged()
        // Moved 300 pt up while the reply grew 200 pt.
        up.endUserScroll(start: sample(0, height: 1000), lastMove: sample(500, height: 1200))
        XCTAssertFalse(up.shouldFollow)
        XCTAssertTrue(up.hasUnreadContent)
        var back = up
        back.beginUserScroll()
        back.contentChanged()
        back.endUserScroll(start: sample(500, height: 1200), lastMove: sample(60, height: 1250))
        XCTAssertTrue(back.shouldFollow)
        XCTAssertFalse(back.hasUnreadContent)
    }

    func testReaderDistanceIgnoresUnknownGeometry() {
        XCTAssertNil(ChatScrollPolicy.readerDistance(start: nil, lastMove: sample(0, height: 10)))
        XCTAssertEqual(ChatScrollPolicy.readerDistance(start: sample(120, height: 10), lastMove: nil), 120)
        XCTAssertNil(ChatScrollPolicy.readerDistance(start: sample(0, height: .infinity), lastMove: nil))
        XCTAssertNil(ChatScrollPolicy.readerDistance(start: sample(0, height: 10), lastMove: sample(.nan, height: 10)))
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
        scrolling.beginUserScroll()
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

private func sample(_ distance: CGFloat, height: CGFloat) -> ChatScrollSample {
    ChatScrollSample(distanceFromBottom: distance, contentHeight: height)
}

private extension ChatScrollPolicy {
    /// A gesture without intermediate movement events and without growth: began and ended at the same distance.
    mutating func gesture(endingAt distance: CGFloat) {
        beginUserScroll()
        endUserScroll(start: sample(distance, height: 1000), lastMove: nil)
    }
}
