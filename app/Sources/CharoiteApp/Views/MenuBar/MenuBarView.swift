import SwiftUI

#if os(macOS)

/// Меню-бар: статус, быстрый вопрос локальной модели, диктовка и заметка.
struct MenuBarView: View {
    @ObservedObject private var sufler = SuflerService.shared
    @ObservedObject private var dictation = DictationService.shared
    @ObservedObject private var chat = LocalChatService.shared
    @Environment(\.openWindow) private var openWindow
    @State private var quick = ""
    @State private var stackNote = ""   // здоровье стека: пусто = всё в порядке

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Чароит").font(.headline)
                Spacer()
                Circle()
                    .fill(sufler.isRunning ? Color.red : (stackNote.isEmpty ? Color.green : Color.orange))
                    .frame(width: 8, height: 8)
                Text(sufler.isRunning ? "Идёт запись" : (stackNote.isEmpty ? "Готов" : stackNote))
                    .font(.caption).foregroundStyle(.secondary)
            }
            // здоровье стека проверяется при открытии меню: молча зелёный,
            // а если Ollama лежит — видно ДО того, как вопрос уйдёт в пустоту
            .task { await checkStack() }

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
                Button {
                    DictationService.shared.toggleDiary()
                } label: {
                    Label("Дневник ⌥⌘J", systemImage: "book.closed")
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

    /// Ollama доступна? Одна лёгкая проверка при открытии меню.
    private func checkStack() async {
        guard let url = URL(string: AppSettings.ollamaURL + "/api/tags") else { return }
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]
        cfg.timeoutIntervalForRequest = 2
        let ok = (try? await URLSession(configuration: cfg).data(from: url)) != nil
        stackNote = ok ? "" : "Ollama не отвечает"
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
