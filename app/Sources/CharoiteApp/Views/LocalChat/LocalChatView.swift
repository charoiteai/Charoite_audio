import SwiftUI

#if os(macOS)

/// Локальный чат: вопросы к модели на этой машине (Ollama), с памятью Чароита.
struct LocalChatView: View {
    // shared: одна история в отдельном окне и в панели суфлёра, персист на диске
    @ObservedObject private var chat = LocalChatService.shared
    @State private var draft = ""
    @FocusState private var inputFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        if chat.messages.isEmpty {
                            VStack(spacing: 10) {
                                Image(systemName: "brain.head.profile")
                                    .font(.largeTitle)
                                    .foregroundStyle(.quaternary)
                                Text("Локальная модель на этой машине. Спроси про проекты, встречи, код —\nс включённой памятью ответ обогащается фактами Чароита.")
                                    .font(.subheadline)
                                    .foregroundStyle(.tertiary)
                                    .multilineTextAlignment(.center)
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.top, 40)
                        }
                        ForEach(chat.messages) { m in
                            bubble(m)
                        }
                        Color.clear.frame(height: 1).id("chatBottom")
                    }
                    .padding(14)
                }
                .onChange(of: chat.messages.count) { _, _ in
                    DispatchQueue.main.async { proxy.scrollTo("chatBottom", anchor: .bottom) }
                }
                .onChange(of: chat.messages.last?.text) { _, _ in
                    DispatchQueue.main.async { proxy.scrollTo("chatBottom", anchor: .bottom) }
                }
            }
            Divider()
            inputBar
        }
        // minWidth здесь НЕ задаём: вью живёт и в отдельном окне (минимум
        // ставит сцена в CharoitApp), и в drawer шириной 430 — жёсткие 520
        // распирали drawer, кнопка «Отправить» уезжала за край окна.
        .frame(minHeight: 320)
        .onDisappear { chat.stopStreaming() }  // закрытое окно не должно держать Ollama 300с
        .task { await chat.refreshModels() }   // пикер моделей — живой список из Ollama
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "brain.head.profile")
                .foregroundStyle(Theme.accent)
            // fixedSize: в панели рядом с суфлёром места мало, и заголовки
            // ломались пополам — «Локальный / чат», «Память / Чароита».
            Text("Локальный чат")
                .font(.headline)
                .fixedSize()
            // 150, не 190: в панели при окне-минимуме заголовок был ШИРЕ
            // самой панели — чат вылезал за край окна. Имя модели длинное —
            // пикер его усечёт, полное видно в раскрытом меню.
            Picker("", selection: $chat.model) {
                ForEach(chat.models, id: \.self) { Text($0).tag($0) }
            }
            .accessibilityLabel("Модель для ответов")
            .frame(width: 150)
            Toggle(isOn: $chat.useMemory) { Text("Память").fixedSize() }
                .toggleStyle(.checkbox)
                .help("Подмешивать факты и граф встреч из памяти Чароита")
                .accessibilityLabel("Память Чароита")
            if !chat.status.isEmpty {
                Text(chat.status)
                    .font(.caption).foregroundStyle(.secondary)
                    .lineLimit(1).truncationMode(.tail)
            }
            Spacer(minLength: 4)
            if chat.isStreaming {
                Button("Стоп") { chat.stopStreaming() }
            }
            Button {
                chat.clear()
            } label: {
                Image(systemName: "trash")
            }
            .help("Очистить диалог")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
    }

    private func bubble(_ m: LocalChatService.Message) -> some View {
        HStack(alignment: .top) {
            if m.role == "user" { Spacer(minLength: 60) }
            // markdown вместо сырых звёздочек: модели пишут **жирное** и `код`
            Text(m.text.isEmpty ? AttributedString("…") : MarkdownLine.render(text: m.text))
                .font(.callout)
                .textSelection(.enabled)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(
                    RoundedRectangle(cornerRadius: Theme.radiusCard)
                        .fill(m.role == "user"
                              ? Theme.accent.opacity(0.14)
                              : Color(nsColor: .quaternarySystemFill))
                )
            if m.role != "user" {
                // копирование целиком: textSelection хорош для куска, кнопка — для всего
                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(m.text, forType: .string)
                } label: {
                    Image(systemName: "doc.on.doc")
                }
                .buttonStyle(.plain)
                .foregroundStyle(.tertiary)
                .help("Скопировать ответ целиком")
                .opacity(m.text.isEmpty ? 0 : 1)
                Spacer(minLength: 40)
            }
        }
    }

    private var inputBar: some View {
        HStack(spacing: 10) {
            // плейсхолдер отличает чат от архивного бара суфлёра: это ДИАЛОГ
            // с памятью, а не разовый вопрос по архиву
            TextField("Диалог с локальной моделью · память Чароита…", text: $draft, axis: .vertical)
                .accessibilityLabel("Вопрос локальной модели")
                .textFieldStyle(.plain)
                .lineLimit(1...5)
                .focused($inputFocused)
                .onSubmit { submit() }
            DictationButton(text: $draft)
            Button("Отправить") { submit() }
                .buttonStyle(.borderedProminent)
                .tint(Theme.accent)
                .keyboardShortcut(.return, modifiers: .command)
                .disabled(chat.isStreaming || draft.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .padding(12)
        .onAppear { inputFocused = true }
    }

    private func submit() {
        chat.send(draft)
        draft = ""
    }
}

#endif
