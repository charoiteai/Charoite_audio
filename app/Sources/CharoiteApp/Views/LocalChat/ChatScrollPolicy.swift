import Foundation

/// One geometry reading of the chat scroll view.
struct ChatScrollSample: Equatable {
    var distanceFromBottom: CGFloat
    var contentHeight: CGFloat
}

/// Layout growth never pauses following by itself.
///
/// Only the reader's own live-scroll gesture (trackpad, scroller drag) and the two buttons pause.
/// The clip view origin is not a pause source: our own jump, lazy row measurement, insets and
/// clamping move it too, and every attempt to tell those apart by geometry paused the chat by itself.
/// A gesture is judged by the reader's last own movement, measured against the bottom as it was
/// when the gesture began: the reply keeps growing while the gesture suppresses our jumps, and
/// that growth is not the reader moving. Without a gesture (mouse wheel without phases, keyboard,
/// window resize) the position can only resume following, never pause it.
struct ChatScrollPolicy {
    static let bottomTolerance: CGFloat = 80

    private(set) var followsLatest = true
    private(set) var isUserScrolling = false
    private(set) var hasUnreadContent = false
    /// The pause button is a decision; only the return button or a new own question undoes it.
    private(set) var pausedByUser = false

    var shouldFollow: Bool { followsLatest && !isUserScrolling }

    mutating func beginUserScroll() { isUserScrolling = true }

    /// Where the reader stopped, from the bottom as it was at the gesture start.
    /// No movement: where the gesture started. Unknown start: nil.
    static func readerDistance(start: ChatScrollSample?, lastMove: ChatScrollSample?) -> CGFloat? {
        guard let start else { return nil }
        let last = lastMove ?? start
        let distance = last.distanceFromBottom - (last.contentHeight - start.contentHeight)
        return distance.isFinite ? distance : nil
    }

    /// The gesture ended (or its end will not come): the reader's last own movement decides.
    /// Unknown geometry is no decision; the previous one stays.
    mutating func endUserScroll(start: ChatScrollSample?, lastMove: ChatScrollSample?) {
        isUserScrolling = false
        guard let distance = Self.readerDistance(start: start, lastMove: lastMove) else { return }
        let atBottom = distance <= Self.bottomTolerance
        if atBottom { hasUnreadContent = false }
        followsLatest = atBottom && !pausedByUser
    }

    /// The observed scroll view is gone mid-gesture: release the latch, keep the decision.
    mutating func cancelUserScroll() { isUserScrolling = false }

    /// Position seen without a gesture: the reader at the bottom resumes following; nothing here pauses.
    mutating func readerSeen(distanceFromBottom distance: CGFloat) {
        guard !followsLatest, !pausedByUser, !isUserScrolling,
              distance.isFinite, distance <= Self.bottomTolerance else { return }
        followsLatest = true
        hasUnreadContent = false
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

    static func distanceFromBottom(document: CGRect, visible: CGRect, flipped: Bool) -> CGFloat {
        let distance = flipped ? document.maxY - visible.maxY : visible.minY - document.minY
        return distance.isFinite ? max(0, distance) : .infinity
    }
}
