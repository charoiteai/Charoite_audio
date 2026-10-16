#if os(macOS)
import AppKit
import SwiftUI

/// Read access to the chat's scroll position outside gestures; nil while no scroll view is attached.
final class ChatScrollProbe {
    fileprivate weak var marker: ChatScrollObserver.Marker?
    var distanceFromBottom: CGFloat? { marker?.distance }
}

/// Scoped to this chat's scroll view. Reports live-scroll gestures only: the start and the end,
/// the end with geometry samples taken at the start and at the reader's last own movement.
/// Movements are kept here, not in SwiftUI state: every scroll frame would rebuild the chat.
struct ChatScrollObserver: NSViewRepresentable {
    var probe: ChatScrollProbe
    var onBegin: () -> Void
    var onEnd: (_ start: ChatScrollSample?, _ lastMove: ChatScrollSample?) -> Void
    /// The observed scroll view is gone mid-gesture: release the gesture latch, never scroll.
    var onCancel: () -> Void

    func makeNSView(context: Context) -> Marker { Marker() }

    func updateNSView(_ view: Marker, context: Context) {
        probe.marker = view
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
        var onEnd: ((ChatScrollSample?, ChatScrollSample?) -> Void)?
        var onCancel: (() -> Void)?
        private weak var observedScrollView: NSScrollView?
        private var inGesture = false
        private var start: ChatScrollSample?
        private var lastMove: ChatScrollSample?

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
            if inGesture, let cancel = onCancel { DispatchQueue.main.async(execute: cancel) }
            observedScrollView = nil
            inGesture = false
            start = nil
            lastMove = nil
        }

        fileprivate var distance: CGFloat? { sample?.distanceFromBottom }

        private var sample: ChatScrollSample? {
            guard let scroll = observedScrollView, let document = scroll.documentView else { return nil }
            let distance = ChatScrollPolicy.distanceFromBottom(document: document.bounds,
                                                               visible: scroll.documentVisibleRect,
                                                               flipped: document.isFlipped)
            let height = document.bounds.height
            guard distance.isFinite, height.isFinite else { return nil }
            return ChatScrollSample(distanceFromBottom: distance, contentHeight: height)
        }

        @objc private func began(_ notification: Notification) {
            inGesture = true
            start = sample
            lastMove = nil
            onBegin?()
        }

        @objc private func scrolled(_ notification: Notification) {
            // Unknown geometry does not overwrite a known movement.
            if inGesture, let now = sample { lastMove = now }
        }

        @objc private func ended(_ notification: Notification) { finish() }

        /// The window closes or resigns key mid-gesture: the end may never come, so the movement
        /// already made decides now. Deferred: a sheet can take key inside a SwiftUI update.
        @objc private func windowLost(_ notification: Notification) {
            guard inGesture, let lost = notification.object as? NSWindow, lost === window else { return }
            DispatchQueue.main.async { [weak self] in self?.finish() }
        }

        private func finish() {
            guard inGesture else { return }
            inGesture = false
            let decided = (start, lastMove)
            start = nil
            lastMove = nil
            onEnd?(decided.0, decided.1)
        }

        deinit { NotificationCenter.default.removeObserver(self) }
    }
}
#endif
