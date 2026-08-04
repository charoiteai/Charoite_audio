import AppKit
import SwiftUI

#if os(macOS)

/// Маленькая кнопка копирования: на секунду подтверждает действие галочкой.
struct SuflerCopyButton: View {
    let text: () -> String
    @State private var copied = false

    var body: some View {
        Button {
            let value = text()
            guard !value.isEmpty else { return }
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(value, forType: .string)
            copied = true
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { copied = false }
        } label: {
            Image(systemName: copied ? "checkmark" : "doc.on.doc")
                .font(.caption)
        }
        .buttonStyle(.plain)
        .help(L.t("Скопировать", "Copy", "复制"))
    }
}

/// Единое пустое состояние панелей: иконка и спокойная поясняющая строка.
struct SuflerEmptyState: View {
    let symbol: String
    let text: String

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: symbol)
                .font(.title2)
                .foregroundStyle(.quaternary)
            Text(text)
                .font(.caption)
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 28)
        .padding(.horizontal, 16)
    }
}

/// Карточка тезиса: решения тёплые, архивный контекст бирюзовый, мысли
/// нейтральные. Старый знак 💎 всё ещё рендерится для прежних записей.
struct SuflerThesisCard: View {
    let text: String

    var body: some View {
        let tint: Color = text.hasPrefix("📌") ? .orange
            : text.hasPrefix("💎") ? Theme.accent
            : text.hasPrefix("⏮") ? .teal
            : .gray
        Text(text)
            .font(.callout)
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(tint.opacity(0.08),
                        in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
            .overlay(alignment: .leading) {
                RoundedRectangle(cornerRadius: 1.5)
                    .fill(tint.opacity(0.55))
                    .frame(width: 3)
                    .padding(.vertical, 5)
            }
    }
}

#endif
