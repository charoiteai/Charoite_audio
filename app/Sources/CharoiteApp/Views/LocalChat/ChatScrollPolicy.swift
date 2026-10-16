import Foundation

/// Layout growth never changes the user's decision to read earlier messages.
struct ChatScrollPolicy {
    static let bottomTolerance: CGFloat = 80

    private(set) var followsLatest = true
    private(set) var isUserScrolling = false
    private(set) var hasUnreadContent = false
    /// The pause button is a decision; only the return button or a new own question undoes it.
    private(set) var pausedByUser = false

    var shouldFollow: Bool { followsLatest && !isUserScrolling }

    mutating func beginUserScroll() { isUserScrolling = true }

    mutating func endUserScroll() { isUserScrolling = false }

    /// The visible position moved: gesture, wheel, keyboard, scroller, VoiceOver or our own jump.
    /// Only a move up past the tolerance pauses, so a programmatic jump down never pauses itself
    /// and growth of the reply during a gesture is not mistaken for leaving the bottom.
    mutating func positionChanged(distanceFromBottom distance: CGFloat, movedUp: Bool) {
        guard distance.isFinite else {
            if movedUp { followsLatest = false }
            return
        }
        if distance <= Self.bottomTolerance {
            guard !pausedByUser else { return }
            followsLatest = true
            hasUnreadContent = false
        } else if movedUp {
            followsLatest = false
        }
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
