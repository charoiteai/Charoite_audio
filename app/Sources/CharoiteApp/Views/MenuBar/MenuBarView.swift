import SwiftUI

#if os(macOS)

/// Меню-бар: статус, быстрый вопрос локальной модели, диктовка и заметка.
struct MenuBarView: View {
    @ObservedObject private var sufler = SuflerService.shared
    @ObservedObject private var dictation = DictationService.shared
    @ObservedObject private var chat = LocalChatService.shared
    @Environment(\.openWindow) private var openWindow
    @State private var quick = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Чароит").font(.headline)
                Spacer()
                Circle()
                    .fill(sufler.isRunning ? Color.red : Color.green)
                    .frame(width: 8, height: 8)
                Text(sufler.isRunning ? "Идёт запись" : "Готов")
                    .font(.caption).foregroundStyle(.secondary)
            }

            HStack(spacing: 8) {
                TextField("Быстрый вопрос…", text: $quick)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit(sendQuick)
                Button {
                    sendQuick()
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                }
                .buttonStyle(.plain)
                .disabled(quick.trimmingCharacters(in: .whitespaces).isEmpty)
            }

            HStack(spacing: 14) {
                Button {
                    DictationService.shared.toggle()
                } label: {
                    Label("Диктовка ⌥⌘D", systemImage: dictation.isRecording ? "mic.fill" : "mic")
                }
                Button {
                    DictationService.shared.toggleNote()
                } label: {
                    Label("Заметка ⌥⌘N", systemImage: "note.text.badge.plus")
                }
            }
            .buttonStyle(.plain)
            .font(.caption)

            if !dictation.status.isEmpty {
                Text(dictation.status).font(.caption2).foregroundStyle(.secondary)
                    .lineLimit(2)
            }

            Divider()

            HStack {
                Button("Открыть") {
                    NSApp.activate(ignoringOtherApps: true)
                    NSApp.windows.first { $0.canBecomeMain }?.makeKeyAndOrderFront(nil)
                }
                Spacer()
                Button {
                    NSApp.terminate(nil)
                } label: {
                    Label("Выход", systemImage: "power")
                }
            }
            .buttonStyle(.plain)
            .font(.callout)
        }
        .padding(14)
        .frame(width: 300)
    }

    /// Быстрый вопрос уходит в общий локальный чат — ответ ждёт в его истории.
    private func sendQuick() {
        let q = quick.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return }
        quick = ""
        chat.send(q)
        openWindow(id: "localchat")
        NSApp.activate(ignoringOtherApps: true)
    }
}

#endif
