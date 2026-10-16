#if os(macOS)
import AppKit
import SwiftUI

/// Read access to the chat's scroll position outside gestures; nil while no scroll view is attached.
final class ChatScrollProbe {
    fileprivate weak var marker: ChatScrollObserver.Marker?
    var distanceFromBottom: CGFloat? { marker?.distance }
}

/// Scoped to this chat's scroll view. Reports live-scroll gestures only: the start, every movement
/// the reader makes (momentum included) and the end, each movement with the distance from the bottom.
struct ChatScrollObserver: NSViewRepresentable {
    var probe: ChatScrollProbe
    var onBegin: (_ distanceFromBottom: CGFloat) -> Void
    var onScroll: (_ distanceFromBottom: CGFloat) -> Void
    var onEnd: () -> Void
    /// The gesture end will not arrive (scroll view gone, window closed or inactive):
    /// release the gesture latch, never scroll.
    var onCancel: () -> Void

    func makeNSView(context: Context) -> Marker { Marker() }

    func updateNSView(_ view: Marker, context: Context) {
        probe.marker = view
        view.onBegin = onBegin
        view.onScroll = onScroll
        view.onEnd = onEnd
        view.onCancel = onCancel
        view.attach()
        // SwiftUI may install the enclosing scroll view after updateNSView.
        DispatchQueue.main.async { [weak view] in view?.attach() }
    }

    static func dismantleNSView(_ view: Marker, coordinator: ()) { view.detach() }

    final class Marker: NSView {
        var onBegin: ((CGFloat) -> Void)?
        var onScroll: ((CGFloat) -> Void)?
        var onEnd: (() -> Void)?
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
            center.addObserver(self, selector: #selector(scrolled), name: NSScrollView.didLiveScrollNotification, object: scroll)
            center.addObserver(self, selector: #selector(ended), name: NSScrollView.didEndLiveScrollNotification, object: scroll)
            // Any window: the marker may move between windows without changing its scroll view.
            center.addObserver(self, selector: #selector(windowLost), name: NSWindow.didResignKeyNotification, object: nil)
            center.addObserver(self, selector: #selector(windowLost), name: NSWindow.willCloseNotification, object: nil)
        }

        func detach() {
            NotificationCenter.default.removeObserver(self)
            // A gesture that began on the old scroll view will never report its end here.
            // Deferred: detach runs inside SwiftUI view updates, where state must not change.
            if observedScrollView != nil, let cancel = onCancel { DispatchQueue.main.async(execute: cancel) }
            observedScrollView = nil
        }

        fileprivate var distance: CGFloat? {
            guard let scroll = observedScrollView, let document = scroll.documentView else { return nil }
            return ChatScrollPolicy.distanceFromBottom(document: document.bounds,
                                                      visible: scroll.documentVisibleRect,
                                                      flipped: document.isFlipped)
        }

        @objc private func began(_ notification: Notification) { onBegin?(distance ?? .infinity) }
        @objc private func scrolled(_ notification: Notification) { onScroll?(distance ?? .infinity) }
        @objc private func ended(_ notification: Notification) { onEnd?() }
        @objc private func windowLost(_ notification: Notification) {
            guard let lost = notification.object as? NSWindow, lost === window else { return }
            onCancel?()
        }

        deinit { NotificationCenter.default.removeObserver(self) }
    }
}
#endif
