import Foundation

/// Layout growth never pauses following by itself.
///
/// Only the reader's own live-scroll gesture (trackpad, scroller drag) and the two buttons pause.
/// The clip view origin is not a pause source: our own jump, lazy row measurement, insets and
/// clamping move it too, and every attempt to tell those apart by geometry paused the chat by itself.
/// Inside a gesture the distance is taken at the reader's own movements only: the reply keeps
/// growing while a gesture suppresses our jumps, and that growth is not the reader leaving.
/// Without a gesture (mouse wheel without phases, keyboard, window resize) the position can only
/// resume following, never pause it.
struct ChatScrollPolicy {
    static let bottomTolerance: CGFloat = 80

    private(set) var followsLatest = true
    private(set) var isUserScrolling = false
    private(set) var hasUnreadContent = false
    /// The pause button is a decision; only the return button or a new own question undoes it.
    private(set) var pausedByUser = false
    /// Distance from the bottom at the reader's last own movement in the current gesture.
    private var readerDistance: CGFloat?

    var shouldFollow: Bool { followsLatest && !isUserScrolling }

    mutating func beginUserScroll(distanceFromBottom distance: CGFloat) {
        isUserScrolling = true
        readerDistance = distance
    }

    /// The reader moved the content (momentum included).
    mutating func userScrolled(distanceFromBottom distance: CGFloat) {
        if isUserScrolling { readerDistance = distance }
    }

    /// The gesture ended: where the reader last moved to is the decision.
    /// Unknown geometry is no decision; the previous one stays.
    mutating func endUserScroll() {
        isUserScrolling = false
        defer { readerDistance = nil }
        guard let distance = readerDistance, distance.isFinite else { return }
        let atBottom = distance <= Self.bottomTolerance
        if atBottom { hasUnreadContent = false }
        followsLatest = atBottom && !pausedByUser
    }

    /// The gesture end is lost (scroll view replaced, window closed or inactive): release the latch, keep the decision.
    mutating func cancelUserScroll() {
        isUserScrolling = false
        readerDistance = nil
    }

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
        readerDistance = nil
    }

    static func distanceFromBottom(document: CGRect, visible: CGRect, flipped: Bool) -> CGFloat {
        let distance = flipped ? document.maxY - visible.maxY : visible.minY - document.minY
        return distance.isFinite ? max(0, distance) : .infinity
    }
}
