import Foundation

/// Семантический индекс графа: bge-m3-вектор на файл через локальную Ollama.
///
/// Лексика ловит идентификаторы, семантика — словарный разрыв: вопрос
/// со внутренним номером системы находит инструкцию, где этого номера
/// нет, а суть описана другими словами. Зеркало серверного
/// brain-индекса: инкрементально по mtime, персист в Application Support,
/// фоновая доиндексация пачками. Ollama без модели bge-m3 — индекс молча
/// пуст, поиск остаётся чисто лексическим (мягкая деградация).
actor SemanticIndex {
    static let shared = SemanticIndex()

    private struct Entry: Codable {
        let mtime: Double
        let vec: [Float]
    }

    private var index: [String: Entry] = [:]   // путь → вектор (нормированный)
    private var loaded = false
    private var indexing = false
    private var generation = 0                 // растёт в useForTests: осиротевший refresh умирает
    private static let headChars = 12000       // суть узла/встречи — в начале файла
    // Кап фоновой доиндексации за вызов: 6 пачек × 8 = 48 файлов. Первый
    // поиск на большом графе иначе ставит в очередь сотни embed-вызовов в
    // ту же Ollama, что обслуживает подсказки живой встречи. Хвост доедет
    // со следующими поисками.
    private static let maxChunksPerRefresh = 6

    // Тестовый шов: подменный эмбеддер и отдельный файл индекса. Продовый
    // путь без useForTests() не меняется. Тесты обязаны подменять ОБА конца:
    // иначе swift test на машине разработчика с поднятой Ollama писал бы
    // эмбеддинги тестовых корпусов в настоящий индекс пользователя.
    private var embedOverride: (([String]) async -> [[Float]]?)?
    private var storeOverride: URL?

    #if DEBUG
    func useForTests(store: URL, embedder: @escaping ([String]) async -> [[Float]]?) async {
        // Актор реентерабелен, а localSearch порождает НЕструктурированный
        // Task.detached с refresh — хвост прошлого теста может жить прямо
        // сейчас, вися на await эмбеддера и держа флаг indexing. gen-токен
        // убивает его ЗАПИСИ, но свежий refresh нового теста дропнулся бы
        // об занятый флаг (guard !indexing) — молча и не каждый раз: ровно
        // так выглядел зелёный push-прогон при красном PR-прогоне того же
        // дерева. Поэтому подмена сначала осиротляет чужой refresh, потом
        // ДОЖИДАЕТСЯ его смерти — тест начинается с тихого индекса.
        generation += 1
        while indexing { await Task.yield() }
        storeOverride = store
        embedOverride = embedder
        index = [:]
        loaded = true      // не подтягивать настоящий файл с диска
    }

    /// Сохранённый mtime записи — проверки инвалидации в тестах.
    func storedMtime(of path: String) -> Double? {
        loadIfNeeded()
        return index[path]?.mtime
    }
    #endif

    private var storeURL: URL {
        if let storeOverride { return storeOverride }
        let dir = FileManager.default.urls(for: .applicationSupportDirectory,
                                           in: .userDomainMask)[0]
            .appendingPathComponent("Charoite", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("semantic_index.json")
    }

    private func embedTexts(_ texts: [String]) async -> [[Float]]? {
        if let embedOverride { return await embedOverride(texts) }
        return await Self.embed(texts)
    }

    private func loadIfNeeded() {
        guard !loaded else { return }
        loaded = true
        guard let data = try? Data(contentsOf: storeURL),
              let decoded = try? JSONDecoder().decode([String: Entry].self, from: data)
        else { return }
        index = decoded
    }

    private func persist() {
        if let data = try? JSONEncoder().encode(index) {
            try? data.write(to: storeURL, options: .atomic)
        }
    }

    /// Похожие файлы: (путь, cosine) по нормированным векторам, порог 0.35.
    func similar(to query: String, within paths: Set<String>, limit: Int) async -> [(String, Double)] {
        loadIfNeeded()
        guard !index.isEmpty,
              let qv = await embedTexts([query])?.first else { return [] }
        let q = Self.unit(qv)
        var sims: [(String, Double)] = []
        for (path, entry) in index where paths.contains(path) {
            var s = 0.0
            for i in 0..<min(q.count, entry.vec.count) { s += Double(q[i]) * Double(entry.vec[i]) }
            if s >= 0.35 { sims.append((path, s)) }
        }
        sims.sort { $0.1 > $1.1 }
        return Array(sims.prefix(limit))
    }

    /// Сколько файлов в индексе — для строки статуса в Настройках.
    func count() -> Int {
        loadIfNeeded()
        return index.count
    }

    /// Лучшая похожесть — сигнал для гейта честности.
    func bestSimilarity(to query: String, within paths: Set<String>) async -> Double {
        await similar(to: query, within: paths, limit: 1).first?.1 ?? 0
    }

    /// Фоновая доиндексация изменившихся файлов (пачки по 8, персист после
    /// каждой — обрыв не теряет прогресс). Одна за раз, не больше
    /// maxChunksPerRefresh пачек за вызов — хвост доедет со следующими.
    func refresh(files: [(path: String, mtime: Double, text: String)]) async {
        loadIfNeeded()
        guard !indexing else { return }
        indexing = true
        defer { indexing = false }
        let gen = generation
        let pending = files.filter { index[$0.path]?.mtime != $0.mtime }
        guard !pending.isEmpty else { return }
        let chunks = stride(from: 0, to: pending.count, by: 8).map {
            Array(pending[$0..<min($0 + 8, pending.count)])
        }.prefix(Self.maxChunksPerRefresh)
        for chunk in chunks {
            guard let embs = await embedTexts(chunk.map { String($0.text.prefix(Self.headChars)) }),
                  embs.count == chunk.count else { return }   // Ollama лежит/нет модели
            // за await эмбеддера индекс могли подменить (useForTests) —
            // осиротевший снапшот не имеет права писать в чужой store
            guard gen == generation else { return }
            for (item, emb) in zip(chunk, embs) {
                index[item.path] = Entry(mtime: item.mtime, vec: Self.unit(emb))
            }
            persist()
        }
    }

    // MARK: - Ollama

    private static func unit(_ v: [Float]) -> [Float] {
        let n = max(sqrt(v.reduce(0) { $0 + $1 * $1 }), 1e-9)
        return v.map { $0 / n }
    }

    private static func embed(_ texts: [String]) async -> [[Float]]? {
        guard let url = URL(string: AppSettings.ollamaURL + "/api/embed") else { return nil }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 60
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "model": "bge-m3", "input": texts, "keep_alive": "30m",
        ] as [String: Any])
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]
        guard let (data, resp) = try? await URLSession(configuration: cfg).data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let embs = obj["embeddings"] as? [[Double]] else { return nil }
        return embs.map { $0.map(Float.init) }
    }
}
