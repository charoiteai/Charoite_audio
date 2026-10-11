#if os(macOS)
import AppKit
import SwiftUI

/// Scoped to this chat's scroll view; content resizing is not a user scroll.
struct ChatScrollObserver: NSViewRepresentable {
    var onBegin: () -> Void
    var onMove: (CGFloat) -> Void
    var onEnd: (CGFloat) -> Void

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
        var onMove: ((CGFloat) -> Void)?
        var onEnd: ((CGFloat) -> Void)?
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
            center.addObserver(self, selector: #selector(moved), name: NSScrollView.didLiveScrollNotification, object: scroll)
            center.addObserver(self, selector: #selector(ended), name: NSScrollView.didEndLiveScrollNotification, object: scroll)
        }

        func detach() {
            NotificationCenter.default.removeObserver(self)
            observedScrollView = nil
        }

        private var distance: CGFloat {
            guard let scroll = observedScrollView, let document = scroll.documentView else { return .infinity }
            return ChatScrollPolicy.distanceFromBottom(document: document.bounds,
                                                      visible: scroll.documentVisibleRect,
                                                      flipped: document.isFlipped)
        }

        @objc private func began(_ notification: Notification) { onBegin?() }
        @objc private func moved(_ notification: Notification) { onMove?(distance) }
        @objc private func ended(_ notification: Notification) { onEnd?(distance) }
        deinit { NotificationCenter.default.removeObserver(self) }
    }
}
#endif
