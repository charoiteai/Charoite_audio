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
    private static let headChars = 12000       // суть узла/встречи — в начале файла

    private var storeURL: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory,
                                           in: .userDomainMask)[0]
            .appendingPathComponent("Charoite", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("semantic_index.json")
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
              let qv = await Self.embed([query])?.first else { return [] }
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
    /// каждой — обрыв не теряет прогресс). Одна за раз.
    func refresh(files: [(path: String, mtime: Double, text: String)]) async {
        loadIfNeeded()
        guard !indexing else { return }
        indexing = true
        defer { indexing = false }
        let pending = files.filter { index[$0.path]?.mtime != $0.mtime }
        guard !pending.isEmpty else { return }
        for chunk in stride(from: 0, to: pending.count, by: 8).map({
            Array(pending[$0..<min($0 + 8, pending.count)])
        }) {
            guard let embs = await Self.embed(chunk.map { String($0.text.prefix(Self.headChars)) }),
                  embs.count == chunk.count else { return }   // Ollama лежит/нет модели
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
