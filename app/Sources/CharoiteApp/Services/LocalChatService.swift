import Foundation

#if os(macOS)

/// Локальный чат с моделью через Ollama (localhost:11434) — ничего не покидает машину.
/// Опция «Память»: перед вопросом ищет по графу встреч (локально, по файлам)
/// и подмешивает найденное в системный промпт.
@MainActor
final class LocalChatService: ObservableObject {
    // Один сервис на приложение: чат в окне суфлёра и отдельное окно видят
    // одну и ту же историю; история переживает перезапуск (JSON на диске)
    static let shared = LocalChatService()

    struct Message: Identifiable, Equatable, Codable {
        var id = UUID()
        let role: String   // "user" | "assistant"
        var text: String
    }

    @Published var messages: [Message] = []

    /// Адрес Ollama — из настроек, а не из строки в коде: настройка, которая
    /// молча ничего не делает, хуже отсутствующей.
    private var ollamaBase: String { AppSettings.ollamaURL }

    private var historyURL: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("CharoiteApp")
        // История цитирует граф — те же права, что у данных встреч: 0700/0600
        // (аудит 16.08, п.2: файл не был ни в карте данных, ни под маской).
        FileManager.default.createPrivateDirectory(at: dir)
        return dir.appendingPathComponent("chat_history.json")
    }

    init() {
        if let data = try? Data(contentsOf: historyURL),
           let saved = try? JSONDecoder().decode([Message].self, from: data) {
            messages = saved
        }
    }

    private func saveHistory() {
        let tail = Array(messages.suffix(200))  // истории хватает, файл не пухнет
        if let data = try? JSONEncoder().encode(tail) {
            // Файл создаётся сразу 0600: write(to:) без .atomic пишет в тот
            // же inode и права не трогает — окна с 0644 по umask нет
            // (круг-1 по PR #377, qwen). makePrivate — для истории,
            // созданной до этой правки.
            if !FileManager.default.fileExists(atPath: historyURL.path) {
                FileManager.default.createPrivateFile(atPath: historyURL.path)
            }
            try? data.write(to: historyURL)
            FileManager.default.makePrivate(atPath: historyURL.path)
        }
    }
    @Published var isStreaming = false
    @Published var model = "qwen3.6:35b-a3b"
    @Published var useMemory = true
    @Published var status = ""

    // живой список из Ollama /api/tags: у пользователя свои модели, хардкод
    // из трёх имён превращал пикер в лотерею «есть ли такая». Фолбэк — прежний.
    @Published var models = ["qwen3.6:35b-a3b", "gemma4:26b", "gemma4:latest"]

    func refreshModels() async {
        guard let url = URL(string: ollamaBase + "/api/tags") else { return }
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]
        cfg.timeoutIntervalForRequest = 3
        guard let (data, _) = try? await URLSession(configuration: cfg).data(from: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let list = obj["models"] as? [[String: Any]] else { return }
        // эмбеддинг-модели в чате бессмысленны — прячем
        let names = list.compactMap { $0["name"] as? String }
            .filter { !$0.contains("bge") && !$0.contains("embed") }
            .sorted()
        guard !names.isEmpty else { return }
        models = names
        if !names.contains(model), let first = names.first { model = first }
    }

    private var task: Task<Void, Never>?

    // Прокси Sweden в Wi-Fi не обходит 127.0.0.1 в URLSession.shared — локальный
    // чат ломался, а «локальные» промпты уходили через внешний прокси
    private static let localSession: URLSession = {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]
        return URLSession(configuration: cfg)
    }()

    func send(_ text: String) {
        let prompt = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty, !isStreaming else { return }
        messages.append(Message(role: "user", text: prompt))
        let assistant = Message(role: "assistant", text: "")
        messages.append(assistant)
        isStreaming = true
        status = ""
        task = Task { await stream(prompt, into: assistant.id) }
    }

    func stopStreaming() {
        task?.cancel()
        isStreaming = false
    }

    func clear() {
        stopStreaming()
        messages = []
        status = ""
        saveHistory()
    }

    // Стрим пишет в СВОЙ пузырь по id: раньше «последнее сообщение» ловило хвост
    // старого ответа после Стоп → новый вопрос
    private func setText(_ id: UUID, _ text: String) {
        if let idx = messages.firstIndex(where: { $0.id == id }) {
            messages[idx].text = text
        }
    }

    private func stream(_ prompt: String, into bubbleId: UUID) async {
        // Промпт локализован целиком: при sufler.language: en интерфейс,
        // панели и поиск по архиву уже отвечали по-английски, а чат — главная
        // фича «спроси про прошлые встречи» — продолжал отвечать по-русски,
        // потому что этого требовала зашитая здесь строка.
        var system = L.t("""
        Ты — Чароит, локальный ассистент владельца этого Mac, работаешь офлайн. \
        Отвечай по-русски, кратко и по делу. ЧЕСТНОСТЬ: отвечай только \
        по данным из блоков ниже и из разговора; если блоки не про то, что спросили, — \
        так и скажи («в памяти этого нет»), не сочиняй. Ты помнишь весь текущий \
        диалог — «а что я спрашивал», «продолжи», «а по второму пункту» относятся \
        к нему. Сопоставляй факты из разных встреч: называй повторяющиеся темы, \
        расхождения и что изменилось со временем, с датами и источниками из блока \
        графа. На вопрос «кто ты» — «Чароит, локальный ассистент», без имени \
        вендора модели.
        """, """
        You are Charoite, the local assistant of this Mac's owner, running offline. \
        Answer in English, briefly and to the point. HONESTY: answer only from the \
        blocks below and from this conversation; if the blocks are not about what \
        was asked, say so ("that is not in memory") and do not invent. You remember \
        the whole current dialogue — "what did I ask", "go on", "what about the \
        second point" refer to it. Cross-reference facts across meetings: name \
        recurring topics, contradictions and what changed over time, with dates and \
        sources from the graph block. To "who are you" — "Charoite, a local \
        assistant", without naming the model vendor.
        """, """
        你是 Charoite，这台 Mac 主人的本地助手，离线运行。用中文简洁地回答。\
        诚实原则：只依据下面的资料块和当前对话作答；如果资料块与所问无关，\
        就直说「记忆中没有这条」，不要编造。你记得整段对话——「我刚才问了什么」\
        「继续」「第二点呢」都指向它。跨会议比对事实：指出反复出现的主题、\
        分歧以及随时间发生的变化，并附上图谱资料块中的日期与来源。\
        被问「你是谁」时回答「Charoite，本地助手」，不要提模型厂商。
        """)
        if useMemory {
            // Поиск по одной последней реплике («а что дальше?») находил мусор:
            // тему разговора несут ПОСЛЕДНИЕ вопросы вместе, свежий — главный
            let topic = (messages.filter { $0.role == "user" }.suffix(3).map(\.text)
                .joined(separator: " ") as String).suffix(500)
            // Бюджет задаётся поиску, а не срезается по хвосту: prefix(5000) резал
            // ПОСЛЕДНИЙ источник на полуслове и мог оставить от него огрызок,
            // при этом первый источник имел право занять сколько угодно.
            var vault = await ArchiveSearch.search(query: String(topic), limit: 8,
                                                   snippet: 800, budget: 5000)
            // маркер слабых совпадений: модель предупреждена, что граф скорее не про это
            let lowConf = vault.hasPrefix(ArchiveSearch.lowConfidenceMarker)
            if lowConf { vault.removeFirst() }
            if !vault.isEmpty {
                system += lowConf
                    ? "\n\n[ГРАФ ВСТРЕЧ — совпадения СЛАБЫЕ, вероятно не про вопрос; не выдавай за факт]\n" + vault
                    : "\n\n[ГРАФ ВСТРЕЧ — люди, системы, решения, документация]\n" + vault
            }
            status = vault.isEmpty ? L.t("в графе пусто по теме", "graph has nothing on the topic", "图谱中无相关内容") : (lowConf ? L.t("граф: слабые совпадения", "graph: weak matches", "图谱：弱匹配") : L.t("🧠 граф подмешан", "🧠 graph mixed in", "🧠 已加入图谱"))
        }
        var msgs: [[String: String]] = [["role": "system", "content": system]]
        // Окно истории: не хвост в N сообщений, а бюджет в знаках — длинные
        // ответы не выталкивают начало разговора мгновенно
        var budget = 12000
        var window: [[String: String]] = []
        for m in messages.reversed() where !m.text.isEmpty {
            let cost = m.text.count
            if budget - cost < 0 { break }
            budget -= cost
            window.append(["role": m.role, "content": m.text])
        }
        msgs.append(contentsOf: window.reversed())

        // Встреча держит большую модель в очереди Ollama (запросы к одной модели
        // сериализуются) — вопрос в чате «висел без реакции» минутами. На время
        // встречи отвечает лёгкая модель: свой слот, мгновенный первый токен.
        var effModel = model
        if SuflerService.shared.isRunning, model == "qwen3.6:35b-a3b" {
            effModel = "qwen3.5:4b"
            status = L.t("встреча идёт — отвечает лёгкая модель (qwen3.5:4b)", "meeting in progress — the light model answers (qwen3.5:4b)", "会议进行中——由轻量模型回答（qwen3.5:4b）")
        }

        guard let url = URL(string: ollamaBase + "/api/chat") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 300
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "model": effModel,
            "messages": msgs,
            "stream": true,
            "think": false,           // критично: дефолтный thinking = ~10с молчания
            "keep_alive": "30m",
            // num_ctx как в OllamaService: без него Modelfile-дефолт 262144 →
            // перезагрузка 23GB модели при переключении чатов и пустой 2-й ответ.
            // 32768 — потолок для 32 ГБ машины (KV-кэш ~3-5 ГБ при 23 ГБ модели):
            // система+граф (5К) + история (12К) + ответ дышат свободно. 64К на
            // 32 ГБ уходит в своп — не поднимать без 64 ГБ RAM.
            "options": ["temperature": 0.35, "num_ctx": 32768, "num_predict": 2048],
        ] as [String: Any])

        do {
            let (bytes, _) = try await Self.localSession.bytes(for: req)
            let throttler = StreamThrottler()  // ~30fps: без него растущий Text = O(n²), 100% CPU
            for try await line in bytes.lines {
                if Task.isCancelled { break }
                guard let data = line.data(using: .utf8),
                      let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
                if let msg = obj["message"] as? [String: Any],
                   let chunk = msg["content"] as? String, !chunk.isEmpty {
                    if let snapshot = await throttler.append(chunk) {
                        setText(bubbleId, snapshot)
                    }
                }
                if obj["done"] as? Bool == true { break }
            }
            let finalText = await throttler.finalText()
            if !finalText.isEmpty { setText(bubbleId, finalText) }
        } catch {
            if !Task.isCancelled, let idx = messages.firstIndex(where: { $0.id == bubbleId }) {
                messages[idx].text += "\n[ошибка: \(error.localizedDescription) — Ollama запущена?]"
            }
        }
        // отменённый стрим не должен разблокировать кнопку под живым новым стримом
        if !Task.isCancelled { isStreaming = false }
        saveHistory()
    }
}

#endif
