import Foundation

#if os(macOS)

/// Чат через настроенный Ollama: по умолчанию localhost, удалённый хост — по явному разрешению.
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
        // Экран «Память» (макет MOBILE_2026-08): что подмешали и откуда ответ.
        // Опциональны с дефолтом nil — старая история декодируется как раньше.
        var sources: [MemoryScreenPolicy.Source]?
        var meta: String?
        var weakMatches: Bool?
        /// «Стоп» оборвал ответ: в истории это обрывок, а не полный ответ.
        var interrupted: Bool?
        /// Служебное пояснение вместо ответа («ответ не пришёл…»): модели не отдаётся.
        var notice: Bool?
    }

    @Published var messages: [Message] = []

    /// Адрес Ollama — из настроек, а не из строки в коде: настройка, которая
    /// молча ничего не делает, хуже отсутствующей.
    private var ollamaBase: String { AppSettings.ollamaURL }
    /// Отвечает ли модель на этом Mac — для подписей экрана чата.
    var inferenceIsLocal: Bool { MemoryScreenPolicy.inferenceIsLocal(base: ollamaBase) }

    private let historyOverride: URL?

    private var historyURL: URL {
        if let historyOverride { return historyOverride }
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("CharoiteApp")
        // История цитирует граф — те же права, что у данных встреч: 0700/0600
        // (аудит 16.08, п.2: файл не был ни в карте данных, ни под маской).
        FileManager.default.createPrivateDirectory(at: dir)
        let url = dir.appendingPathComponent("chat_history.json")
        // История старой установки — закрыть при первом же обращении, а не
        // ждать следующего сохранения (круг-1 по PR #377, Codex).
        if FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.makePrivate(atPath: url.path)
        }
        return url
    }

    init(historyURL: URL? = nil) {
        historyOverride = historyURL
        if let data = try? Data(contentsOf: self.historyURL),
           let saved = try? JSONDecoder().decode([Message].self, from: data) {
            messages = saved
        }
    }

    private func saveHistory() {
        // Пустой пузырь ответа — заглушка потока, а не сообщение: в файле он
        // навсегда остался бы «…» после перезапуска.
        let kept = messages.filter { !($0.role == "assistant" && $0.text.isEmpty && $0.meta == nil) }
        let tail = Array(kept.suffix(200))  // истории хватает, файл не пухнет
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
    /// Пузырь, чей поток уже получил `done`: «Стоп» после этого ответ не обрывает.
    var completedBubble: UUID?
    @Published var model = "qwen3.6:35b-a3b"
    @Published var useMemory = true
    @Published var status = ""

    // живой список из Ollama /api/tags: у пользователя свои модели, хардкод
    // из трёх имён превращал пикер в лотерею «есть ли такая». Фолбэк — прежний.
    @Published var models = ["qwen3.6:35b-a3b", "gemma4:26b", "gemma4:latest"]

    /// nil — ещё не спрашивали; true/false — Ollama ответила/нет. Питает
    /// строку шапки «Ollama отвечает»; где она — на этом Mac или на сервере из
    /// настроек — говорит `inferenceIsLocal`, а не эта строка.
    @Published private(set) var ollamaAlive: Bool?

    func refreshModels() async {
        guard let url = URL(string: ollamaBase + "/api/tags") else { return }
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]
        cfg.timeoutIntervalForRequest = 3
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await URLSession(configuration: cfg).data(from: url)
        } catch {
            // Отменённая проба ничего не наблюдала; правило шапки одно с ответом чата
            guard !Task.isCancelled else { return }
            ollamaAlive = ChatAnswerOutcome.serverAlive(after: error)
            return
        }
        guard let list = ChatAnswerOutcome.probeModels(status: (response as? HTTPURLResponse)?.statusCode,
                                                       data: data) else {
            ollamaAlive = nil       // ответил не Ollama или не так — живость не доказана
            return
        }
        ollamaAlive = true
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

    @discardableResult
    func send(_ text: String) -> Bool {
        let prompt = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !prompt.isEmpty, !isStreaming else { return false }
        // Вопрос длиннее окна модели Ollama обрезал бы молча, и ответ пришёл бы
        // не на заданный вопрос. Черновик остаётся в поле ввода.
        guard prompt.count <= ChatContextWindow.maxPrompt else {
            status = L.t("Вопрос слишком длинный: \(prompt.count) знаков, предел \(ChatContextWindow.maxPrompt). Сократите его.",
                         "The question is too long: \(prompt.count) characters, limit \(ChatContextWindow.maxPrompt). Please shorten it.",
                         "问题过长：\(prompt.count) 个字符，上限 \(ChatContextWindow.maxPrompt)。请缩短。")
            return false
        }
        messages.append(Message(role: "user", text: prompt))
        let assistant = Message(role: "assistant", text: "")
        messages.append(assistant)
        isStreaming = true
        status = ""
        // Вопрос — в файл сразу: закрытое посреди ответа приложение его не теряет
        // (пустой пузырь ответа фильтр `saveHistory` не пишет).
        saveHistory()
        task = Task { await stream(prompt, into: assistant.id) }
        return true
    }

    func stopStreaming() {
        task?.cancel()
        if isStreaming, let idx = messages.lastIndex(where: { $0.role == "assistant" }),
           messages[idx].id != completedBubble {
            if messages[idx].text.isEmpty && messages[idx].meta == nil {
                // «…» до первого токена — заглушка, а не ответ: экран и файл одинаковы
                messages.remove(at: idx)
            } else {
                messages[idx].interrupted = true
            }
        }
        isStreaming = false
        // A canceled memory lookup can return before stream's final save.
        saveHistory()
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

    /// Строка статуса — из вердикта поиска, тем же решением, что и правило
    /// честности в промпте: «пусто» не пишется там, где поиск был неполным
    /// или графа нет (№634).
    nonisolated static func memoryStatus(_ context: ChatRetrieval.Context) -> String { context.status }

    private func stream(_ prompt: String, into bubbleId: UUID) async {
        // Snapshot the configured endpoint before asynchronous retrieval.
        let inferenceBase = ollamaBase
        let inferenceIsLocal = MemoryScreenPolicy.inferenceIsLocal(base: inferenceBase)
        // Основа промпта и блок графа — в ChatRetrieval: пробы и бенч памяти
        // собирают ход тем же кодом (№632).
        var system = ChatRetrieval.baseSystem(inferenceIsLocal: inferenceIsLocal)
        let startedAt = Date()
        // Тумблер снят ОДИН раз на старте: переключение памяти во время
        // генерации не должно переписывать происхождение уже собранного
        // контекста (круг-1, Codex).
        let memoryOn = useMemory
        var chipSources: [MemoryScreenPolicy.Source] = []
        var sourcesInContext = 0
        var weakMatches = false
        var searchIncomplete = false
        if memoryOn {
            let topic = ChatRetrieval.query(from: messages)
            // Сосед по [[ссылке]] без слов запроса — только модели на этом Mac
            // (№695, постфактум-2 по #776).
            let params = ChatRetrieval.Params.chat
            let outcome = await ArchiveSearch.searchStructured(
                query: topic, limit: params.limit, snippet: params.snippet, budget: params.budget,
                includeLinked: inferenceIsLocal)
            guard !Task.isCancelled else { return }
            let context = ChatRetrieval.contextBlock(outcome)
            let vault = context.text
            let lowConf = context.hidesSources
            // Чипы под ответом — точный список хитов поиска (Outcome.rels);
            // упоминание пути в чужом сниппете источником не считается
            // (круг-1: DS/Codex/GLM). Brain-ветка структуры не отдаёт —
            // честный запасной разбор текста. Слабые совпадения чипами не
            // выдаём — их нельзя показывать как опору ответа.
            weakMatches = context.weak
            searchIncomplete = context.searchIncomplete
            let found = outcome.rels.map { rels in
                rels.map { MemoryScreenPolicy.Source(rel: $0, kind: MemoryScreenPolicy.kind(of: $0)) }
            } ?? MemoryScreenPolicy.sources(from: vault)
            // Чипы и счёт — ОДИН список: потолок задаёт лимит поиска (8),
            // отдельный потолок чипов давал мете «7 источников» при шести
            // видимых (круг-2, DS).
            sourcesInContext = lowConf ? 0 : found.count
            chipSources = lowConf ? [] : found
            system += context.block
            status = Self.memoryStatus(context)
        } else {
            system += ChatRetrieval.memoryOffBlock()
        }
        guard !Task.isCancelled else { return }
        var msgs: [[String: String]] = [["role": "system", "content": system]]
        // Окно истории: не хвост в N сообщений, а бюджет в знаках — длинные
        // ответы не выталкивают начало разговора мгновенно
        let currentUserIndex = messages.firstIndex(where: { $0.id == bubbleId })
            .map { max(0, $0 - 1) } ?? messages.count
        let history = Self.contextHistory(messages.prefix(currentUserIndex))
        let window = ChatContextWindow.select(history: history, prompt: prompt)
        msgs.append(contentsOf: window.map { ["role": $0.role, "content": $0.text] })

        // Встреча держит большую модель в очереди Ollama (запросы к одной модели
        // сериализуются) — вопрос в чате «висел без реакции» минутами. На время
        // встречи отвечает лёгкая модель: свой слот, мгновенный первый токен.
        var effModel = model
        if SuflerService.shared.isRunning, model == "qwen3.6:35b-a3b" {
            effModel = "qwen3.5:4b"
            status = L.t("встреча идёт — отвечает лёгкая модель (qwen3.5:4b)", "meeting in progress — the light model answers (qwen3.5:4b)", "会议进行中——由轻量模型回答（qwen3.5:4b）")
        }

        guard let url = URL(string: inferenceBase + "/api/chat") else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 300
        req.httpBody = try? JSONSerialization.data(
            withJSONObject: ChatRetrieval.requestBody(model: effModel, messages: msgs, stream: true))

        var sawDone = false
        var doneReason: String?
        var failure: Error?
        let throttler = StreamThrottler()  // ~30fps: без него растущий Text = O(n²), 100% CPU
        do {
            let (bytes, resp) = try await Self.localSession.bytes(for: req)
            // 404/500 Ollama — это тело ошибки без «done», и EOF выглядел
            // как успех: пустой пузырь получал мету и зелёный чип (круг-2,
            // Codex). Ошибка HTTP — ошибка, а не тихий конец стрима.
            // Ответил живой сервер: его причина (`model not found`), а не «Ollama запущена?».
            if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
                throw await Self.serverRefusal(bytes, status: http.statusCode)
            }
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
                // Ошибка посреди 200-стрима — как в src/llm.py: причина, а не
                // «соединение закрыто» (DS, круг 4 по #804)
                if let err = obj["error"] as? String { throw ChatStreamError(message: err) }
                if obj["done"] as? Bool == true {
                    sawDone = true
                    doneReason = obj["done_reason"] as? String
                    completedBubble = bubbleId
                    break
                }
            }
        } catch {
            failure = error
        }
        // Хвост буфера — и после ошибки: дошедший текст не теряется (круг 5 по #804)
        let finalText = await throttler.finalText()
        if !finalText.isEmpty { setText(bubbleId, finalText) }
        // Исход — одно решение по фактам потока (`ChatAnswerOutcome`): пометка
        // «ответ прерван», строка происхождения и пустой пузырь выводятся из него.
        let outcome = ChatAnswerOutcome.decide(sawDone: sawDone, doneReason: doneReason,
                                               cancelled: Task.isCancelled, failed: failure != nil)
        let secs = max(1, Int(Date().timeIntervalSince(startedAt).rounded()))
        apply(outcome, to: bubbleId, failure: failure.map(ChatAnswerOutcome.failureNote),
              sources: chipSources.isEmpty ? nil : chipSources, weakMatches: weakMatches) {
            MemoryScreenPolicy.metaLine(
                model: effModel, seconds: secs,
                meetingsInContext: chipSources.filter { $0.kind == .meeting }.count,
                sourcesInContext: sourcesInContext,
                memoryOn: memoryOn, weakMatches: weakMatches, searchIncomplete: searchIncomplete,
                inferenceIsLocal: inferenceIsLocal)
        }
        switch outcome {
        case .complete, .truncated:
            // Поток дошёл до done — Ollama жива; статус графа отработал
            // своё (круг-1, Codex). Статус принадлежит этому ответу, если поток
            // не отменён или новый вопрос ещё не начался.
            ollamaAlive = true
            if !Task.isCancelled || !isStreaming { status = "" }
        case .failed:
            // Решает только этот отказ (`serverAlive`), прежнее не удерживается
            ollamaAlive = ChatAnswerOutcome.headerAfterFailure(failure)
        case .dropped:
            // Обрыв без ответа: жив ли сервер, неизвестно — шапка молчит, а не врёт
            ollamaAlive = nil
        case .stopped:
            break
        }
        // отменённый стрим не должен разблокировать кнопку под живым новым стримом
        if !Task.isCancelled { isStreaming = false }
        saveHistory()
    }

    /// История для модели: служебные пояснения — не ответы модели, их нет в контексте.
    nonisolated static func contextHistory(_ messages: ArraySlice<Message>) -> [ChatContextWindow.Entry] {
        messages.map { ChatContextWindow.Entry(role: $0.role, text: $0.notice == true ? "" : $0.text) }
    }

    /// Ответ не 200: первые ~4000 знаков тела. Причина из `{"error": …}` — `ChatStreamError` со
    /// статусом, без неё — `ChatHTTPError`; в обоих случаях кто ответил, не доказано.
    nonisolated static func serverRefusal(_ bytes: URLSession.AsyncBytes, status: Int) async -> Error {
        var body = ""
        do {
            for try await line in bytes.lines {
                body += line
                if body.count > 4000 { break }
            }
        } catch {}
        // Причину прислала Ollama — сервер жив; без неё (прокси, чужой сервер) — это HTTP, не модель
        if let reason = serverReason(body: body) { return ChatStreamError(message: reason, status: status) }
        return ChatHTTPError(status: status)
    }

    /// Причина отказа из тела не-200 ответа Ollama (`{"error": …}`); нет её — nil.
    nonisolated static func serverReason(body: String) -> String? {
        if let data = body.data(using: .utf8),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let reason = obj["error"] as? String,
           !reason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return reason
        }
        return nil
    }

    /// Пузырь по исходу потока: заглушка уходит, неполный помечен, ошибка и
    /// пустой обрыв названы, строка происхождения — только у дошедшего текста.
    private func apply(_ outcome: ChatAnswerOutcome, to bubbleId: UUID, failure: String?,
                       sources: [MemoryScreenPolicy.Source]?, weakMatches: Bool, meta: () -> String) {
        guard let idx = messages.firstIndex(where: { $0.id == bubbleId }) else { return }
        let empty = messages[idx].text.isEmpty
        if empty {
            switch outcome.emptyBubble(failure: failure) {
            case .remove:
                messages.remove(at: idx)
                return
            case .keep:
                break
            case .explain(let reason):
                messages[idx].text = reason
                messages[idx].notice = true
            }
        } else if outcome == .failed, let failure {
            messages[idx].text += "\n" + failure
        }
        messages[idx].interrupted = outcome.marksInterrupted(textIsEmpty: empty) ? true : nil
        // Строка происхождения и чипы: у каждого числа источник (модель —
        // какая отвечала, время — замер, встречи — счёт подмешанных).
        if outcome.getsMeta(textIsEmpty: empty) {
            messages[idx].sources = sources
            messages[idx].weakMatches = weakMatches ? true : nil
            messages[idx].meta = meta()
        }
    }
}

#endif
