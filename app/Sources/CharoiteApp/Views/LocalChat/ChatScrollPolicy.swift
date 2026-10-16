import Foundation

/// Layout growth never changes the user's decision to read earlier messages.
///
/// Only the reader's own live-scroll gesture (trackpad, scroller drag) and the two buttons decide.
/// The clip view origin is not a decision source: our own jump, lazy row measurement, insets and
/// clamping move it too, and every attempt to tell those apart by geometry paused the chat by itself.
/// A mouse wheel without phases and keyboard paging send no gesture: they behave as before this
/// change, and the pause button covers them.
struct ChatScrollPolicy {
    static let bottomTolerance: CGFloat = 80

    private(set) var followsLatest = true
    private(set) var isUserScrolling = false
    private(set) var hasUnreadContent = false
    /// The pause button is a decision; only the return button or a new own question undoes it.
    private(set) var pausedByUser = false

    var shouldFollow: Bool { followsLatest && !isUserScrolling }

    mutating func beginUserScroll() { isUserScrolling = true }

    /// The gesture ended, momentum included: where the reader stopped is the decision.
    mutating func endUserScroll(distanceFromBottom distance: CGFloat) {
        isUserScrolling = false
        let atBottom = distance.isFinite && distance <= Self.bottomTolerance
        if atBottom { hasUnreadContent = false }
        followsLatest = atBottom && !pausedByUser
    }

    /// The observed scroll view is gone mid-gesture: release the latch, keep the decision.
    mutating func cancelUserScroll() { isUserScrolling = false }

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

    static func distanceFromBottom(document: CGRect, visible: CGRect, flipped: Bool) -> CGFloat {
        let distance = flipped ? document.maxY - visible.maxY : visible.minY - document.minY
        return distance.isFinite ? max(0, distance) : .infinity
    }
}
