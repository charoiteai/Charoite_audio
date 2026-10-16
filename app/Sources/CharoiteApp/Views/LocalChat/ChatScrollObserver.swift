#if os(macOS)
import AppKit
import SwiftUI

/// Scoped to this chat's scroll view; content resizing is not a user scroll.
/// Only live-scroll gestures are reported; the end carries the reader's distance from the bottom.
struct ChatScrollObserver: NSViewRepresentable {
    var onBegin: () -> Void
    var onEnd: (_ distanceFromBottom: CGFloat) -> Void
    /// The observed scroll view is gone mid-gesture: release the gesture latch, never scroll.
    var onCancel: () -> Void

    func makeNSView(context: Context) -> Marker { Marker() }

    func updateNSView(_ view: Marker, context: Context) {
        view.onBegin = onBegin
        view.onEnd = onEnd
        view.onCancel = onCancel
        view.attach()
        // SwiftUI may install the enclosing scroll view after updateNSView.
        DispatchQueue.main.async { [weak view] in view?.attach() }
    }

    static func dismantleNSView(_ view: Marker, coordinator: ()) { view.detach() }

    final class Marker: NSView {
        var onBegin: (() -> Void)?
        var onEnd: ((CGFloat) -> Void)?
        var onCancel: (() -> Void)?
        private weak var observedScrollView: NSScrollView?

        override func hitTest(_ point: NSPoint) -> NSView? { nil }
        override func viewDidMoveToWindow() { super.viewDidMoveToWindow(); attach() }
        override func viewDidMoveToSuperview() { super.viewDidMoveToSuperview(); attach() }

        func attach() {
            guard let scroll = enclosingScrollView, scroll !== observedScrollView else { return }
            detach()
            observedScrollView = scroll
            let center = NotificationCenter.default
            center.addObserver(self, selector: #selector(began), name: NSScrollView.willStartLiveScrollNotification, object: scroll)
            center.addObserver(self, selector: #selector(ended), name: NSScrollView.didEndLiveScrollNotification, object: scroll)
        }

        func detach() {
            NotificationCenter.default.removeObserver(self)
            // A gesture that began on the old scroll view will never report its end here.
            // Deferred: detach runs inside SwiftUI view updates, where state must not change.
            if observedScrollView != nil, let cancel = onCancel { DispatchQueue.main.async(execute: cancel) }
            observedScrollView = nil
        }

        private var distance: CGFloat {
            guard let scroll = observedScrollView, let document = scroll.documentView else { return .infinity }
            return ChatScrollPolicy.distanceFromBottom(document: document.bounds,
                                                      visible: scroll.documentVisibleRect,
                                                      flipped: document.isFlipped)
        }

        @objc private func began(_ notification: Notification) { onBegin?() }
        @objc private func ended(_ notification: Notification) { onEnd?(distance) }

        deinit { NotificationCenter.default.removeObserver(self) }
    }
}
#endif
