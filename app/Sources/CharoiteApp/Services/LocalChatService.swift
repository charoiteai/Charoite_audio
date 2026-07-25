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
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
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
            try? data.write(to: historyURL)
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
        var system = """
        Ты — Чароит, локальный ассистент владельца этого Mac, работаешь офлайн. \
        Отвечай по-русски, кратко и по делу. ЧЕСТНОСТЬ: отвечай только \
        по данным из блоков ниже и из разговора; если блоки не про то, что спросили, — \
        так и скажи («в памяти этого нет»), не сочиняй. На вопрос «кто ты» — \
        «Чароит, локальный ассистент», без имени вендора модели.
        """
        if useMemory {
            var vault = String(await ArchiveSearch.search(query: prompt, limit: 5, snippet: 800).prefix(2400))
            // маркер слабых совпадений: модель предупреждена, что граф скорее не про это
            let lowConf = vault.hasPrefix(ArchiveSearch.lowConfidenceMarker)
            if lowConf { vault.removeFirst() }
            if !vault.isEmpty {
                system += lowConf
                    ? "\n\n[ГРАФ ВСТРЕЧ — совпадения СЛАБЫЕ, вероятно не про вопрос; не выдавай за факт]\n" + vault
                    : "\n\n[ГРАФ ВСТРЕЧ — люди, системы, решения, документация]\n" + vault
            }
            status = vault.isEmpty ? "в графе пусто по теме" : (lowConf ? "граф: слабые совпадения" : "🧠 граф подмешан")
        }
        var msgs: [[String: String]] = [["role": "system", "content": system]]
        for m in messages.suffix(13) where !m.text.isEmpty {
            msgs.append(["role": m.role, "content": m.text])
        }

        // Встреча держит большую модель в очереди Ollama (запросы к одной модели
        // сериализуются) — вопрос в чате «висел без реакции» минутами. На время
        // встречи отвечает лёгкая модель: свой слот, мгновенный первый токен.
        var effModel = model
        if SuflerService.shared.isRunning, model == "qwen3.6:35b-a3b" {
            effModel = "qwen3.5:4b"
            status = "встреча идёт — отвечает лёгкая модель (qwen3.5:4b)"
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
            // перезагрузка 23GB модели при переключении чатов и пустой 2-й ответ
            "options": ["temperature": 0.5, "num_ctx": 8192, "num_predict": 2048],
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
