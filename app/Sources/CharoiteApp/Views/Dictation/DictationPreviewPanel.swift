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
    private var flashHide: Task<Void, Never>?

    private init() {}

    func show(text: String, hint: String) {
        flashHide?.cancel()
        flashHide = nil
        model.text = text
        model.hint = hint
        guard !text.isEmpty else { return }
        let panel = panel ?? makePanel()
        self.panel = panel
        // Экран выбирается один раз на диктовку — при первом показе; иначе
        // плашка бегала бы за курсором между дисплеями посреди фразы.
        if !panel.isVisible {
            place(panel)
            panel.orderFrontRegardless()
        }
    }

    func hide() {
        flashHide?.cancel()
        flashHide = nil
        panel?.orderOut(nil)
        model.text = ""
    }

    /// Показать на несколько секунд и спрятать: в поле ушёл черновик, и
    /// человек, глядя в чужое окно, должен увидеть, чей это текст. Новая
    /// диктовка (`show`) или `hide` снимают таймер.
    func flash(text: String, hint: String, seconds: Double) {
        show(text: text, hint: hint)
        flashHide = Task { [weak self] in
            try? await Task.sleep(for: .seconds(seconds))
            guard !Task.isCancelled else { return }
            self?.hide()
        }
    }

    private func makePanel() -> NSPanel {
        // Высота с запасом на три строки .title3 и подсказку (~110 pt при
        // 15 pt); плашка прижата к низу, растёт вверх, низ не прыгает.
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 560, height: 132),
                            styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
        panel.level = .floating
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.ignoresMouseEvents = true
        panel.hidesOnDeactivate = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
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
                    .font(.title3)
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
            .frame(maxHeight: .infinity, alignment: .bottom)
        }
    }
}

#endif
