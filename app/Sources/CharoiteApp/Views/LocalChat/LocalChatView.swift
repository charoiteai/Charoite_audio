import SwiftUI

#if os(macOS)

/// Локальный чат: вопросы к модели на этой машине (Ollama), с памятью Чароита.
struct LocalChatView: View {
    // shared: одна история в отдельном окне и в панели суфлёра, персист на диске
    @ObservedObject private var chat = LocalChatService.shared
    @State private var draft = ""
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
                    MemoryInventoryColumn(snapshot: inventory.snapshot)
                        .frame(width: 250)
                }
            }
        }
        .frame(minHeight: 320)
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
                                       text: L.t("Локальная модель на этой машине. С включённой памятью ответ опирается на встречи, граф и досье.",
                                                 "A local model on this Mac. With memory on, answers lean on meetings, the graph and dossiers.",
                                                 "运行在本机的本地模型。开启记忆后，回答会依据会议、图谱与档案。"),
                                       systemImage: "brain.head.profile") {
                                HStack(spacing: 8) {
                                    ForEach(Self.starterQuestions, id: \.self) { q in
                                        Button(q) {
                                            draft = q
                                            inputFocused = true
                                        }
                                        .charoite(.regular, .s)
                                    }
                                }
                            }
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
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: "brain.head.profile")
                .foregroundStyle(Theme.accent)
            // fixedSize: в панели рядом с суфлёром места мало, и заголовки
            // ломались пополам — «Локальный / чат», «Память / Чароита».
            Text(L.t("Локальный чат", "Local chat", "本地聊天"))
                .font(.headline)
                .fixedSize()
            // 150, не 190: в панели при окне-минимуме заголовок был ШИРЕ
            // самой панели — чат вылезал за край окна. Имя модели длинное —
            // пикер его усечёт, полное видно в раскрытом меню.
            Picker("", selection: $chat.model) {
                ForEach(chat.models, id: \.self) { Text($0).tag($0) }
            }
            .accessibilityLabel(L.t("Модель для ответов", "Answering model", "回答所用模型"))
            .frame(width: 150)
            // Чип-состояние вместо системного чекбокса (макет MOBILE_2026-08,
            // экран «Память»; правило LayerChip из встречи): включённая память
            // графа — индиго, выключенная — серая.
            LayerChip(title: L.t("Память графа", "Graph memory", "图谱记忆"), isOn: $chat.useMemory)
                .help(L.t("Подмешивать факты и граф встреч из памяти Чароита", "Mix in facts and the meeting graph from Charoite's memory", "混入 Charoite 记忆中的事实与会议图谱"))
                .accessibilityLabel(L.t("Память Чароита", "Charoite memory", "Charoite 记忆"))
            if !chat.status.isEmpty {
                Text(chat.status)
                    .font(.caption).foregroundStyle(.secondary)
                    .lineLimit(1).truncationMode(.tail)
            } else {
                // Честная строка происхождения (макет: «Ollama отвечает ·
                // 0 запросов в сеть»): «отвечает» — по факту живого списка
                // моделей, «0 запросов в сеть» — факт, а не обещание: чужой
                // адрес Ollama приложение отвергает
                // (AppSettings.ollamaURLRejection). Живёт в слоте статуса и
                // ужимается первой (ревью 22.08).
                Text(chat.ollamaAlive == false
                     ? L.t("Ollama не отвечает", "Ollama is not responding", "Ollama 无响应")
                     : L.t("Ollama отвечает · 0 запросов в сеть", "Ollama responding · 0 network calls", "Ollama 正常 · 0 次外网请求"))
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(chat.ollamaAlive == false ? Theme.warning : Theme.ok)
                    .lineLimit(1).truncationMode(.tail)
                    .padding(.horizontal, 7).padding(.vertical, 2)
                    .background(Capsule().fill((chat.ollamaAlive == false ? Theme.warning : Theme.ok).opacity(0.12)))
                    .layoutPriority(-1)
            }
            Spacer(minLength: 4)
            if chat.isStreaming {
                Button(L.t("Стоп", "Stop", "停止")) { chat.stopStreaming() }
            }
            Button {
                chat.clear()
            } label: {
                Image(systemName: "trash")
            }
            .help(L.t("Очистить диалог", "Clear the chat", "清空对话"))
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
            SourceChipsRow(sources: sources)
        } else if m.weakMatches == true {
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
            TextField(L.t("Диалог с локальной моделью · память Чароита…", "Chat with the local model · Charoite memory…", "与本地模型对话 · Charoite 记忆…"), text: $draft, axis: .vertical)
                .accessibilityLabel(L.t("Вопрос локальной модели", "Question to the local model", "向本地模型提问"))
                .textFieldStyle(.plain)
                .lineLimit(1...5)
                .focused($inputFocused)
                .onSubmit { submit() }
            DictationButton(text: $draft)
            Button(L.t("Отправить", "Send", "发送")) { submit() }
                .charoite(.prominent)
                .keyboardShortcut(.return, modifiers: .command)
                .disabled(chat.isStreaming || draft.trimmingCharacters(in: .whitespaces).isEmpty)
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
        chat.send(draft)
        draft = ""
    }
}

#endif
