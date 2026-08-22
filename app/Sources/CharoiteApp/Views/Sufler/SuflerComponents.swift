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
/// Пустая нить встречи — через общий `EmptyState` (правило 2 ревизии):
/// заголовок, что появится, и что нажать. Сигнатура прежняя, вызов в
/// SuflerView не тронут.
struct SuflerEmptyState: View {
    let symbol: String
    let text: String

    var body: some View {
        EmptyState(text,
                   text: L.t("Стенограмма, тезисы и подсказки появятся здесь по ходу разговора.",
                             "Transcript, theses and hints show up here as the conversation goes.",
                             "逐字稿、要点与提示会随着对话出现在这里。"),
                   systemImage: symbol)
    }
}

#endif
