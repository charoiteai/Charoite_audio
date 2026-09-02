import AppKit
import SwiftUI

#if os(macOS)

/// Плашка живого черновика диктовки: внизу экрана, поверх всех окон,
/// не крадёт фокус и не ловит мышь — человек диктует в чужое поле по
/// ⌥⌘D, и его окно обязано остаться активным. Появляется с первым словом,
/// исчезает, когда GigaAM отдал финал.
@MainActor
final class DictationPreviewPanel {
    static let shared = DictationPreviewPanel()

    private final class Model: ObservableObject {
        @Published var text = ""
        @Published var hint = ""
    }

    private let model = Model()
    private var panel: NSPanel?

    private init() {}

    func show(text: String, hint: String) {
        model.text = text
        model.hint = hint
        guard !text.isEmpty else { return }
        let panel = panel ?? makePanel()
        self.panel = panel
        place(panel)
        if !panel.isVisible { panel.orderFrontRegardless() }
    }

    func hide() {
        panel?.orderOut(nil)
        model.text = ""
    }

    private func makePanel() -> NSPanel {
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 560, height: 96),
                            styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
        panel.level = .floating
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.ignoresMouseEvents = true
        panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient]
        panel.contentView = NSHostingView(rootView: PreviewHUD(model: model))
        return panel
    }

    /// Низ экрана с курсором — там, где человек сейчас работает.
    private func place(_ panel: NSPanel) {
        let mouse = NSEvent.mouseLocation
        let screen = NSScreen.screens.first { $0.frame.contains(mouse) } ?? NSScreen.main
        guard let frame = screen?.visibleFrame else { return }
        let size = panel.frame.size
        let origin = NSPoint(x: frame.midX - size.width / 2, y: frame.minY + 48)
        panel.setFrameOrigin(origin)
    }

    private struct PreviewHUD: View {
        @ObservedObject var model: Model

        var body: some View {
            VStack(alignment: .leading, spacing: 4) {
                Text(model.text)
                    .font(.system(size: 15))
                    .lineLimit(3)
                    .truncationMode(.head)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if !model.hint.isEmpty {
                    Text(model.hint)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .frame(width: 560, alignment: .leading)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .padding(6)
        }
    }
}

#endif
