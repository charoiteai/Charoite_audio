import Foundation

/// Layout growth never changes the user's decision to read earlier messages.
struct ChatScrollPolicy {
    static let bottomTolerance: CGFloat = 80
    /// Sub-point remainder of a clamp or a fractional layout is still "at the bottom".
    static let atBottom: CGFloat = 1

    private(set) var followsLatest = true
    private(set) var isUserScrolling = false
    private(set) var hasUnreadContent = false
    /// The pause button is a decision; only the return button or a new own question undoes it.
    private(set) var pausedByUser = false

    var shouldFollow: Bool { followsLatest && !isUserScrolling }

    mutating func beginUserScroll() { isUserScrolling = true }

    mutating func endUserScroll() { isUserScrolling = false }

    /// The visible position moved: gesture, wheel, keyboard, scroller, VoiceOver or our own jump.
    /// Direction decides, not distance: any move up away from the bottom pauses (one wheel tick
    /// is 10-60 pt), and only a move down into the tolerance resumes. Our own jump only moves
    /// down; layout growth does not move the origin; a shrinking document clamps the origin up
    /// but lands at the bottom (distance 0), so neither is taken for the reader leaving.
    mutating func positionChanged(distanceFromBottom distance: CGFloat, movedUp: Bool) {
        if movedUp {
            if !distance.isFinite || distance > Self.atBottom { followsLatest = false }
            return
        }
        guard distance.isFinite, distance <= Self.bottomTolerance else { return }
        hasUnreadContent = false
        if !pausedByUser { followsLatest = true }
    }

    @discardableResult
    mutating func contentChanged() -> Bool {
        if !shouldFollow { hasUnreadContent = true }
        return shouldFollow
    }

    mutating func pause() {
        pausedByUser = true
        followsLatest = false
    }

    /// Return button and own question: the reader explicitly asks for the latest content.
    mutating func resume() {
        pausedByUser = false
        followsLatest = true
        isUserScrolling = false
        hasUnreadContent = false
    }

    /// Clip view origin moved towards the start of the document.
    static func movedUp(originY: CGFloat, previousOriginY: CGFloat, flipped: Bool) -> Bool {
        flipped ? originY < previousOriginY : originY > previousOriginY
    }

    static func distanceFromBottom(document: CGRect, visible: CGRect, flipped: Bool) -> CGFloat {
        let distance = flipped ? document.maxY - visible.maxY : visible.minY - document.minY
        return distance.isFinite ? max(0, distance) : .infinity
    }
}
