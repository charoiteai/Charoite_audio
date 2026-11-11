import Foundation

/// Read before a text update; geometry refreshes this only after layout/scroll.
struct MeetingFeedScroll {
    private(set) var nearBottom = true
    private var previous: MeetingFeedGeometry?

    mutating func observe(bottom: CGFloat, viewport: CGFloat) {
        nearBottom = bottom <= viewport + 48 && bottom >= -48
    }

    @discardableResult
    mutating func observe(_ geometry: MeetingFeedGeometry) -> Bool {
        defer { previous = geometry }
        guard let previous else {
            observe(bottom: max(0, geometry.contentHeight - geometry.offset - geometry.viewport), viewport: 0)
            return false
        }
        guard abs(geometry.offset - previous.offset) > 0.5 else { return false }
        // A smaller document/changed viewport can clamp the old offset. Frame
        // and bounds notifications may arrive separately; this is layout, not
        // inferred reader movement, and must not cancel a new card's reveal.
        let layoutOffset = min(max(0, previous.offset), max(0, geometry.contentHeight - geometry.viewport))
        guard abs(geometry.offset - layoutOffset) > 0.5 else { return false }
        // Attribute growth separately from the actual movement using the existing chat policy.
        // A coalesced document resize must not erase the reader's simultaneous scroll.
        let start = ChatScrollSample(distanceFromBottom: previous.contentHeight - previous.offset - previous.viewport,
                                     contentHeight: previous.contentHeight)
        let last = ChatScrollSample(distanceFromBottom: geometry.contentHeight - geometry.offset - geometry.viewport,
                                    contentHeight: geometry.contentHeight)
        if let distance = ChatScrollPolicy.readerDistance(start: start, lastMove: last) {
            observe(bottom: max(0, distance), viewport: 0)
        }
        return true
    }

    func followsUpdate(manualCardOpen: Bool) -> Bool {
        nearBottom && !manualCardOpen
    }
}

struct MeetingFeedGeometry: Equatable {
    var contentHeight: CGFloat = 0
    var offset: CGFloat = 0
    var viewport: CGFloat = 0
}

/// Native scroll callbacks update intent synchronously without rebuilding SwiftUI.
final class MeetingFeedReader {
    private var intent = MeetingFeedScroll()
    private var isUserScrolling = false
    private(set) var revision: UInt64 = 0

    func beginUserScroll() { isUserScrolling = true; revision &+= 1 }
    func cancelUserScroll() { isUserScrolling = false }
    func endUserScroll(start: ChatScrollSample?, lastMove: ChatScrollSample?) {
        guard isUserScrolling else { return }
        isUserScrolling = false
        if let distance = ChatScrollPolicy.readerDistance(start: start, lastMove: lastMove) {
            intent.observe(bottom: max(0, distance), viewport: 0)
        }
    }

    func observe(_ sample: ChatScrollSample, viewport: CGFloat) {
        if intent.observe(.init(contentHeight: sample.contentHeight,
                                offset: sample.contentHeight - sample.distanceFromBottom - viewport,
                                viewport: viewport)) { revision &+= 1 }
    }

    func permitsRequestedJump(since requestedRevision: UInt64) -> Bool {
        revision == requestedRevision && !isUserScrolling
    }

    func followsUpdate(manualCardOpen: Bool) -> Bool {
        !isUserScrolling && intent.followsUpdate(manualCardOpen: manualCardOpen)
    }

    func reset() {
        intent = MeetingFeedScroll(); isUserScrolling = false; revision &+= 1
    }
}
