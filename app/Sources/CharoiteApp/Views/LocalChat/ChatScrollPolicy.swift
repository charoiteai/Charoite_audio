import Foundation

/// Layout growth never changes the user's decision to read earlier messages.
struct ChatScrollPolicy {
    private(set) var followsLatest = true
    private(set) var isUserScrolling = false
    private(set) var hasUnreadContent = false

    var shouldFollow: Bool { followsLatest && !isUserScrolling }

    mutating func beginUserScroll() { isUserScrolling = true }

    mutating func updateUserScroll(distanceFromBottom: CGFloat) {
        followsLatest = distanceFromBottom.isFinite && distanceFromBottom <= 80
        if followsLatest { hasUnreadContent = false }
    }

    mutating func endUserScroll(distanceFromBottom: CGFloat) {
        updateUserScroll(distanceFromBottom: distanceFromBottom)
        isUserScrolling = false
    }

    @discardableResult
    mutating func contentChanged() -> Bool {
        if !shouldFollow { hasUnreadContent = true }
        return shouldFollow
    }

    mutating func pause() { followsLatest = false }

    mutating func resume() {
        followsLatest = true
        hasUnreadContent = false
    }

    static func distanceFromBottom(document: CGRect, visible: CGRect, flipped: Bool) -> CGFloat {
        let distance = flipped ? document.maxY - visible.maxY : visible.minY - document.minY
        return distance.isFinite ? max(0, distance) : .infinity
    }
}
