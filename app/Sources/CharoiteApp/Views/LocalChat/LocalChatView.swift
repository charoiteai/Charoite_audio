import SwiftUI

#if os(macOS)

/// Локальный чат: вопросы к модели на этой машине (Ollama), с памятью Чароита.
struct LocalChatView: View {
    // shared: одна история в отдельном окне и в панели суфлёра, персист на диске
    @ObservedObject private var chat = LocalChatService.shared
    @State private var draft = ""
    @State private var confirmClear = false
    @State private var scrollPolicy = ChatScrollPolicy()
    @FocusState private var inputFocused: Bool

    @ObservedObject private var inventory = GraphInventoryService.shared

    var body: some View {
        // Правая колонка «Что знает память» — только там, где ей есть место:
        // в широком workspace. В drawer 430 рядом с суфлёром — только чат.
        GeometryReader { geo in
            HStack(spacing: 0) {
                chatColumn
                if geo.size.width >= 760 {
                    Divider()
                    MemoryInventoryColumn(snapshot: inventory.snapshot, configured: inventory.configured)
                        .frame(width: 250)
                }
            }
        }
        .frame(minHeight: 320)
        .alert(L.t("Очистить диалог?", "Clear the conversation?", "清空对话？"),
               isPresented: $confirmClear) {
            Button(L.t("Отмена", "Cancel", "取消"), role: .cancel) {}
            Button(L.t("Очистить диалог", "Clear conversation", "清空对话"), role: .destructive) {
                chat.clear()
            }
        } message: {
            Text(L.t("Сообщения будут удалены из сохранённой истории чата. Записи встреч и память графа останутся. Отменить очистку нельзя.",
                     "Messages will be removed from saved chat history. Meeting recordings and graph memory will remain. This cannot be undone.",
                     "消息将从保存的聊天历史中删除。会议录音和图谱记忆将保留。此操作无法撤销。"))
        }
        .onDisappear { chat.stopStreaming() }  // закрытое окно не должно держать Ollama 300с
        .task {
            await chat.refreshModels()   // пикер моделей — живой список из Ollama
            inventory.refresh()
        }
    }

    private var chatColumn: some View {
        VStack(spacing: 0) {
            header
            Divider()
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        if chat.messages.isEmpty {
                            // Три вопроса-подсказки сразу показывают жанр: чем
                            // это отличается от разового вопроса по архиву.
                            EmptyState(title: L.t("Память Чароита", "Charoite memory", "Charoite 记忆"),
                                       text: MemoryScreenPolicy.chatIntro(inferenceIsLocal: chat.inferenceIsLocal),
                                       systemImage: "brain.head.profile") {
                                ViewThatFits(in: .horizontal) {
                                    HStack(spacing: 8) { starterButtons }
                                    VStack(alignment: .leading, spacing: 8) { starterButtons }
                                }
                            }
                        }
                        ForEach(chat.messages) { m in
                            bubble(m)
                        }
                        Color.clear.frame(height: 1).id("chatBottom")
                    }
                    .padding(14)
                    .background(ChatScrollObserver(
                        onBegin: { scrollPolicy.beginUserScroll() },
                        onMove: { scrollPolicy.updateUserScroll(distanceFromBottom: $0) },
                        onEnd: { distance in
                            scrollPolicy.endUserScroll(distanceFromBottom: distance)
                            if scrollPolicy.shouldFollow { scrollToLatest(using: proxy) }
                        }
                    ))
                }
                .onAppear { scrollToLatest(using: proxy) }
                .onChange(of: chat.messages.count) { _, count in
                    if count == 0 { scrollPolicy = ChatScrollPolicy() }
                    if scrollPolicy.contentChanged() { scrollToLatest(using: proxy) }
                }
                .onChange(of: chat.messages.last?.text) { _, _ in
                    if scrollPolicy.contentChanged() { scrollToLatest(using: proxy) }
                }
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    HStack(spacing: 8) {
                        if scrollPolicy.followsLatest {
                            Button {
                                scrollPolicy.pause()
                            } label: {
                                Label(L.t("Приостановить прокрутку", "Pause auto-scroll", "暂停自动滚动"), systemImage: "pause")
                            }
                        } else {
                            Text(L.t("Прокрутка приостановлена", "Auto-scroll paused", "自动滚动已暂停"))
                                .foregroundStyle(.secondary)
                            Spacer(minLength: 0)
                            Button {
                                scrollPolicy.resume()
                                scrollToLatest(using: proxy)
                            } label: {
                                Label(scrollPolicy.hasUnreadContent
                                      ? L.t("Новый ответ ↓", "New response ↓", "新回复 ↓")
                                      : L.t("Вниз ↓", "To latest ↓", "到底部 ↓"), systemImage: "arrow.down")
                            }
                            .help(L.t("Вернуться вниз и включить автопрокрутку", "Return to the bottom and resume auto-scroll", "返回底部并恢复自动滚动"))
                        }
                    }
                    .font(.caption)
                    .buttonStyle(.borderless)
                    .padding(.horizontal, 14).padding(.vertical, 6)
                    .frame(maxWidth: .infinity, alignment: .trailing)
                    .background(.bar)
                }
            }
            Divider()
            inputBar
        }
        // minWidth здесь НЕ задаём: вью живёт и в отдельном окне (минимум
        // ставит сцена в CharoitApp), и в drawer шириной 430 — жёсткие 520
        // распирали drawer, кнопка «Отправить» уезжала за край окна.
    }

    private var starterButtons: some View {
        ForEach(Self.starterQuestions, id: \.self) { question in
            Button(question) {
                draft = question
                inputFocused = true
            }
            .charoite(.regular, .s)
        }
    }

    private func scrollToLatest(using proxy: ScrollViewProxy) {
        DispatchQueue.main.async {
            // A gesture can start after the stream scheduled this update.
            guard scrollPolicy.shouldFollow else { return }
            proxy.scrollTo("chatBottom", anchor: .bottom)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Image(systemName: "brain.head.profile").foregroundStyle(Theme.accent)
                Text(L.t("Чат с памятью", "Chat with memory", "记忆聊天"))
                    .font(.headline)
                Spacer(minLength: 4)
                if chat.isStreaming {
                    Button(L.t("Стоп", "Stop", "停止")) { chat.stopStreaming() }
                }
                Button {
                    confirmClear = true
                } label: { Image(systemName: "trash") }
                .disabled(chat.messages.isEmpty)
                .help(L.t("Очистить диалог", "Clear the chat", "清空对话"))
                .accessibilityLabel(L.t("Очистить диалог", "Clear the chat", "清空对话"))
            }
            HStack(spacing: 10) {
                Picker("", selection: $chat.model) {
                    ForEach(chat.models, id: \.self) { Text($0).tag($0) }
                }
                .accessibilityLabel(L.t("Модель для ответов", "Answering model", "回答所用模型"))
                .frame(minWidth: 100, maxWidth: 180)
                LayerChip(title: L.t("Память графа", "Graph memory", "图谱记忆"), isOn: $chat.useMemory)
                    .help(L.t("Использовать материалы встреч, графа и досье при ответе",
                              "Use meeting, graph and dossier materials when answering", "回答时使用会议、图谱和档案资料"))
                    .accessibilityLabel(L.t("Память Чароита", "Charoite memory", "Charoite 记忆"))
                Spacer(minLength: 0)
            }
            if !chat.status.isEmpty {
                Text(chat.status).font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else if let alive = chat.ollamaAlive {
                Text(alive
                     ? L.t("Ollama отвечает", "Ollama is responding", "Ollama 正常")
                     : L.t("Ollama не отвечает", "Ollama is not responding", "Ollama 无响应"))
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(alive ? Theme.ok : Theme.warning)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
    }

    private func bubble(_ m: LocalChatService.Message) -> some View {
        HStack(alignment: .top) {
            if m.role == "user" { Spacer(minLength: 60) }
            // markdown вместо сырых звёздочек: модели пишут **жирное** и `код`
            let body = Text(m.text.isEmpty ? AttributedString("…") : MarkdownLine.render(text: m.text))
                .font(.callout)
                .textSelection(.enabled)
            if m.role == "user" {
                body
                    .padding(.horizontal, 12)
                    .padding(.vertical, 8)
                    .background(RoundedRectangle(cornerRadius: Theme.radiusCard)
                        .fill(Theme.accent.opacity(0.14)))
            } else {
                // Ответ — белая карточка с волосяной рамкой, как карточки
                // встреч в библиотеке (гамма экранов встреч, решение 24.08).
                // Происхождение из памяти несут чипы источников и строка
                // мета — не цвет поверхности.
                VStack(alignment: .leading, spacing: 6) {
                    body
                    answerFooter(m)
                }
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(RoundedRectangle(cornerRadius: Theme.radiusCard, style: .continuous)
                    .fill(Color(nsColor: .controlBackgroundColor)))
                .overlay(RoundedRectangle(cornerRadius: Theme.radiusCard, style: .continuous)
                    .strokeBorder(Color.primary.opacity(0.10), lineWidth: 1))
            }
            if m.role != "user" {
                // копирование целиком: textSelection хорош для куска, кнопка — для всего
                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(m.text, forType: .string)
                } label: {
                    Image(systemName: "doc.on.doc")
                }
                .charoite(.icon, .s)
                .help(L.t("Скопировать ответ целиком", "Copy the whole answer", "复制完整回答"))
                .opacity(m.text.isEmpty ? 0 : 1)
                Spacer(minLength: 40)
            }
        }
    }

    /// Подвал ответа: чипы источников и строка происхождения. Чип встречи
    /// ведёт в библиотеку на её карточку, узел и досье открываются файлом.
    @ViewBuilder
    private func answerFooter(_ m: LocalChatService.Message) -> some View {
        if let sources = m.sources, !sources.isEmpty {
            Text(L.t("Материалы, переданные модели", "Materials provided to the model", "提供给模型的资料"))
                .font(.caption2).foregroundStyle(.secondary)
            SourceChipsRow(sources: sources)
        }
        if m.weakMatches == true {
            Text(L.t("⚠ совпадения в графе слабые", "⚠ weak graph matches", "⚠ 图谱匹配较弱"))
                .font(.caption2.weight(.semibold))
                .foregroundStyle(Theme.warning)
                .padding(.horizontal, 8).padding(.vertical, 2)
                .background(Capsule().fill(Theme.warning.opacity(0.12)))
        }
        if let meta = m.meta {
            Divider().opacity(0.6)
            Text(meta)
                .font(.caption2).foregroundStyle(.secondary)
        }
    }

    private var inputBar: some View {
        HStack(spacing: 10) {
            // плейсхолдер отличает чат от архивного бара суфлёра: это ДИАЛОГ
            // с памятью, а не разовый вопрос по архиву
            TextField(MemoryScreenPolicy.inputPlaceholder(inferenceIsLocal: chat.inferenceIsLocal),
                      text: $draft, axis: .vertical)
                .accessibilityLabel(MemoryScreenPolicy.inputLabel(inferenceIsLocal: chat.inferenceIsLocal))
                .textFieldStyle(.plain)
                .lineLimit(1...5)
                .focused($inputFocused)
                .onSubmit { submit() }
            DictationButton(text: $draft)
            Button(L.t("Отправить", "Send", "发送")) { submit() }
                .charoite(.prominent)
                .keyboardShortcut(.return, modifiers: .command)
                .disabled(chat.isStreaming || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(12)
        .onAppear { inputFocused = true }
    }

    /// Вопросы, которые показывают жанр экрана (ревизия 08.08, экран 5).
    static var starterQuestions: [String] {
        [L.t("Что просрочено?", "What is overdue?", "哪些已逾期？"),
         L.t("Бриф на утро", "Morning brief", "晨间简报"),
         L.t("О чём молчим третью встречу?", "What have we dodged for three meetings?", "连续三次会议都在回避什么？")]
    }

    private func submit() {
        Self.submitDraft(&draft, using: chat)
    }

    /// Both Enter and the button use this transition. Keep a refused draft.
    @MainActor
    static func submitDraft(_ draft: inout String, using chat: LocalChatService) {
        if chat.send(draft) { draft = "" }
    }
}

#endif
