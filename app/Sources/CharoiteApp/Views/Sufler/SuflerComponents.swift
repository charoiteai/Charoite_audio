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
    let running: Bool

    var body: some View {
        EmptyState(running
                       ? L.t("Слушаю…", "Listening…", "聆听中…")
                       : L.t("Встреча ещё не началась", "The meeting has not started", "会议尚未开始"),
                   text: running
                       ? L.t("Стенограмма и подсказки появятся здесь по ходу разговора.",
                             "Transcript and hints show up here as the conversation goes.",
                             "逐字稿与提示会随着对话出现在这里。")
                       : L.t("Нажмите «Слушать встречу» (⌘⇧␣) — стенограмма и подсказки появятся здесь.",
                             "Press “Listen to the meeting” (⌘⇧␣) — transcript and hints appear here.",
                             "点按「旁听会议」(⌘⇧␣)——逐字稿与提示会出现在这里。"),
                   systemImage: symbol)
    }
}

#endif
