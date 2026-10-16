#if os(macOS)
import AppKit
import SwiftUI

/// Scoped to this chat's scroll view; content resizing is not a user scroll.
/// Position comes from the clip view bounds, so the mouse wheel without phases, keyboard
/// paging, scroller arrows and VoiceOver are seen too, not only live-scroll gestures.
struct ChatScrollObserver: NSViewRepresentable {
    var onBegin: () -> Void
    var onMove: (_ distanceFromBottom: CGFloat, _ movedUp: Bool) -> Void
    var onEnd: () -> Void

    func makeNSView(context: Context) -> Marker { Marker() }

    func updateNSView(_ view: Marker, context: Context) {
        view.onBegin = onBegin
        view.onMove = onMove
        view.onEnd = onEnd
        view.attach()
        // SwiftUI may install the enclosing scroll view after updateNSView.
        DispatchQueue.main.async { [weak view] in view?.attach() }
    }

    static func dismantleNSView(_ view: Marker, coordinator: ()) { view.detach() }

    final class Marker: NSView {
        var onBegin: (() -> Void)?
        var onMove: ((CGFloat, Bool) -> Void)?
        var onEnd: (() -> Void)?
        private weak var observedScrollView: NSScrollView?
        private var lastOriginY: CGFloat?

        override func hitTest(_ point: NSPoint) -> NSView? { nil }
        override func viewDidMoveToWindow() { super.viewDidMoveToWindow(); attach() }
        override func viewDidMoveToSuperview() { super.viewDidMoveToSuperview(); attach() }

        func attach() {
            guard let scroll = enclosingScrollView, scroll !== observedScrollView else { return }
            detach()
            observedScrollView = scroll
            lastOriginY = scroll.contentView.bounds.origin.y
            scroll.contentView.postsBoundsChangedNotifications = true
            let center = NotificationCenter.default
            center.addObserver(self, selector: #selector(began), name: NSScrollView.willStartLiveScrollNotification, object: scroll)
            center.addObserver(self, selector: #selector(ended), name: NSScrollView.didEndLiveScrollNotification, object: scroll)
            center.addObserver(self, selector: #selector(boundsChanged), name: NSView.boundsDidChangeNotification, object: scroll.contentView)
        }

        func detach() {
            NotificationCenter.default.removeObserver(self)
            // A gesture that began on the old scroll view will never report its end here.
            if observedScrollView != nil, let end = onEnd { DispatchQueue.main.async(execute: end) }
            observedScrollView = nil
            lastOriginY = nil
        }

        private var distance: CGFloat {
            guard let scroll = observedScrollView, let document = scroll.documentView else { return .infinity }
            return ChatScrollPolicy.distanceFromBottom(document: document.bounds,
                                                      visible: scroll.documentVisibleRect,
                                                      flipped: document.isFlipped)
        }

        @objc private func began(_ notification: Notification) { onBegin?() }
        @objc private func ended(_ notification: Notification) { onEnd?() }

        @objc private func boundsChanged(_ notification: Notification) {
            guard let scroll = observedScrollView else { return }
            let originY = scroll.contentView.bounds.origin.y
            defer { lastOriginY = originY }
            guard let last = lastOriginY, originY != last else { return }
            let flipped = scroll.documentView?.isFlipped ?? true
            onMove?(distance, flipped ? originY < last : originY > last)
        }

        deinit { NotificationCenter.default.removeObserver(self) }
    }
}
#endif
