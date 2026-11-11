import AppKit
import XCTest
@testable import CharoiteApp

final class MeetingFeedScrollTests: XCTestCase {
    func testSmallNearBottomReaderMovementInvalidatesOnlyTheOldQueuedScroll() {
        let reader = MeetingFeedReader()
        reader.observe(.init(distanceFromBottom: 12, contentHeight: 1400), viewport: 400)
        let scheduled = reader.revision
        reader.observe(.init(distanceFromBottom: 32, contentHeight: 1400), viewport: 400)
        XCTAssertTrue(reader.followsUpdate(manualCardOpen: false))
        XCTAssertFalse(reader.permitsRequestedJump(since: scheduled))
        XCTAssertTrue(reader.permitsRequestedJump(since: reader.revision))
    }

    func testLateFirstSampleAlreadyAboveBottomDoesNotFollowAnAppend() {
        let reader = MeetingFeedReader()
        reader.observe(.init(distanceFromBottom: 400, contentHeight: 1400), viewport: 400)
        XCTAssertFalse(reader.followsUpdate(manualCardOpen: false))
        reader.observe(.init(distanceFromBottom: 600, contentHeight: 1600), viewport: 400)
        XCTAssertFalse(reader.followsUpdate(manualCardOpen: false))
    }

    func testInitialEmptyDocumentStillFollowsItsFirstOverflow() {
        let reader = MeetingFeedReader()
        reader.observe(.init(distanceFromBottom: -400, contentHeight: 0), viewport: 400)
        reader.observe(.init(distanceFromBottom: 1000, contentHeight: 1400), viewport: 400)
        XCTAssertTrue(reader.followsUpdate(manualCardOpen: false))
    }

    func testLayoutShrinkClampingDoesNotCancelTheNewExplicitQuestionJump() {
        let reader = MeetingFeedReader()
        reader.observe(.init(distanceFromBottom: 12, contentHeight: 2000), viewport: 400)
        let request = reader.revision
        // Frame and bounds notifications may arrive separately: first the new
        // smaller document, then AppKit clamps the previous offset into it.
        reader.observe(.init(distanceFromBottom: -1388, contentHeight: 600), viewport: 400)
        reader.observe(.init(distanceFromBottom: 0, contentHeight: 600), viewport: 400)
        XCTAssertTrue(reader.permitsRequestedJump(since: request))
    }

    func testQueuedExplicitQuestionJumpCannotOverrideNewerReaderMovement() {
        let reader = MeetingFeedReader()
        reader.observe(.init(distanceFromBottom: 12, contentHeight: 1451), viewport: 653.5)
        let request = reader.revision
        XCTAssertTrue(reader.permitsRequestedJump(since: request))
        reader.observe(.init(distanceFromBottom: 450, contentHeight: 1451), viewport: 653.5)
        XCTAssertFalse(reader.permitsRequestedJump(since: request))
        let laterRequest = reader.revision
        reader.beginUserScroll()
        XCTAssertFalse(reader.permitsRequestedJump(since: laterRequest))
        reader.cancelUserScroll()
        reader.reset()
        XCTAssertFalse(reader.permitsRequestedJump(since: laterRequest))
    }

    func testUserMovementDuringGrowthIsNotDiscarded() {
        var state = MeetingFeedScroll()
        state.observe(.init(contentHeight: 1200, offset: 800, viewport: 400))
        state.observe(.init(contentHeight: 1400, offset: 600, viewport: 400))
        XCTAssertFalse(state.followsUpdate(manualCardOpen: false))
    }

    @MainActor
    func testLateAttachSamplesBeforeTheFirstUserMovement() {
        final class Flipped: NSView { override var isFlipped: Bool { true } }
        let scroll = NSScrollView(frame: .init(x: 0, y: 0, width: 300, height: 400))
        let document = Flipped(frame: .init(x: 0, y: 0, width: 300, height: 1200))
        scroll.documentView = document
        scroll.contentView.setBoundsOrigin(.init(x: 0, y: 800))
        let marker = ChatScrollObserver.Marker(frame: .zero)
        let reader = MeetingFeedReader()
        var samples = 0
        marker.onPosition = { sample, viewport in
            samples += 1
            reader.observe(sample, viewport: viewport)
        }
        marker.attach()  // no enclosing scroll view yet
        XCTAssertEqual(samples, 0)
        document.addSubview(marker)  // successful late attachment must sample now
        XCTAssertGreaterThan(samples, 0)
        scroll.contentView.setBoundsOrigin(.init(x: 0, y: 200))
        XCTAssertEqual(scroll.contentView.bounds.origin.y, 200)
        XCTAssertFalse(reader.followsUpdate(manualCardOpen: false))
        marker.detach()
    }

    @MainActor
    func testOptInNativeObserverReportsBoundsAndDetaches() {
        let scroll = NSScrollView(frame: .init(x: 0, y: 0, width: 300, height: 400))
        let document = NSView(frame: .init(x: 0, y: 0, width: 300, height: 1200))
        scroll.documentView = document
        let marker = ChatScrollObserver.Marker(frame: .zero)
        document.addSubview(marker)
        scroll.contentView.postsBoundsChangedNotifications = false
        document.postsFrameChangedNotifications = false
        var samples: [ChatScrollSample] = []
        marker.onPosition = { sample, viewport in
            XCTAssertGreaterThan(viewport, 0)
            samples.append(sample)
        }
        marker.attach()
        marker.reportPosition()
        let initial = samples.count
        scroll.contentView.setBoundsOrigin(.init(x: 0, y: 200))
        XCTAssertEqual(scroll.contentView.bounds.origin.y, 200)
        let deadline = Date().addingTimeInterval(1)
        while samples.count == initial && Date() < deadline {
            RunLoop.main.run(until: Date().addingTimeInterval(0.01))
        }
        XCTAssertGreaterThan(samples.count, initial)
        let afterScroll = samples.count
        marker.detach()
        XCTAssertFalse(scroll.contentView.postsBoundsChangedNotifications)
        XCTAssertFalse(document.postsFrameChangedNotifications)
        scroll.contentView.setBoundsOrigin(.init(x: 0, y: 300))
        XCTAssertEqual(samples.count, afterScroll)
    }

    func testNativeReaderKeepsPhaseLessMovementAndGrowthSeparate() {
        let reader = MeetingFeedReader()
        reader.observe(.init(distanceFromBottom: 12, contentHeight: 1451), viewport: 653.5)
        reader.observe(.init(distanceFromBottom: 450, contentHeight: 1451), viewport: 653.5)
        XCTAssertFalse(reader.followsUpdate(manualCardOpen: false))
        reader.observe(.init(distanceFromBottom: 630, contentHeight: 1631), viewport: 653.5)
        XCTAssertFalse(reader.followsUpdate(manualCardOpen: false))
        reader.observe(.init(distanceFromBottom: 0, contentHeight: 1631), viewport: 653.5)
        XCTAssertTrue(reader.followsUpdate(manualCardOpen: false))
        reader.observe(.init(distanceFromBottom: 180, contentHeight: 1811), viewport: 653.5)
        XCTAssertTrue(reader.followsUpdate(manualCardOpen: false))
        XCTAssertFalse(reader.followsUpdate(manualCardOpen: true))
        reader.reset()
        XCTAssertTrue(reader.followsUpdate(manualCardOpen: false))
    }

    func testContentGrowthPreservesReaderIntentUntilAnActualScroll() {
        var state = MeetingFeedScroll()
        state.observe(.init(contentHeight: 400, offset: 0, viewport: 400))
        state.observe(.init(contentHeight: 1200, offset: 0, viewport: 400))
        XCTAssertTrue(state.followsUpdate(manualCardOpen: false), "initial growth must follow")
        state.observe(.init(contentHeight: 1200, offset: 800, viewport: 400))
        state.observe(.init(contentHeight: 1400, offset: 800, viewport: 400))
        XCTAssertTrue(state.followsUpdate(manualCardOpen: false), "growth at the bottom preserves follow")
        state.observe(.init(contentHeight: 1400, offset: 500, viewport: 400))
        state.observe(.init(contentHeight: 1600, offset: 500, viewport: 400))
        XCTAssertFalse(state.followsUpdate(manualCardOpen: false), "reading above survives growth")
        state.observe(.init(contentHeight: 1600, offset: 1200, viewport: 400))
        state.observe(.init(contentHeight: 1800, offset: 1200, viewport: 400))
        XCTAssertTrue(state.followsUpdate(manualCardOpen: false), "returning down resumes follow")
        XCTAssertFalse(state.followsUpdate(manualCardOpen: true))
    }

    func testScrollUpStopsFollowingAndReturningDownResumes() {
        var state = MeetingFeedScroll()
        state.observe(bottom: 400, viewport: 400)
        XCTAssertTrue(state.followsUpdate(manualCardOpen: false))
        state.observe(bottom: 900, viewport: 400)
        XCTAssertFalse(state.followsUpdate(manualCardOpen: false))
        state.observe(bottom: 430, viewport: 400)
        XCTAssertTrue(state.followsUpdate(manualCardOpen: false))
        XCTAssertFalse(state.followsUpdate(manualCardOpen: true))
    }
}
