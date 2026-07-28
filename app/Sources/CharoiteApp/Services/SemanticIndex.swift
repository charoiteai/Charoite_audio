import Foundation

/// Семантический индекс графа: bge-m3-вектор на БЛОК файла через локальную Ollama.
///
/// Лексика ловит идентификаторы, семантика — словарный разрыв: вопрос
/// со внутренним номером системы находит инструкцию, где этого номера
/// нет, а суть описана другими словами. Инкрементально по mtime, персист в
/// Application Support, фоновая доиндексация пачками. Ollama без модели
/// bge-m3 — индекс молча пуст, поиск остаётся чисто лексическим (мягкая
/// деградация).
///
/// Почему на блок, а не на файл. Раньше строился один вектор по первым
/// 12 000 знакам, и для узла это работало, а для стенограммы — нет: решения
/// принимают в конце. Замер по рабочему графу — 325 файлов из 1172 длиннее
/// этой границы, 63% содержимого не попадало в индекс вообще. Плюс Ollama
/// молча режет вход около 12 300 знаков (проверено: маркер в конце не меняет
/// вектор), так что «просто поднять лимит» было не вариантом.
///
/// Хранение бинарное. 4785 блоков рабочего графа в JSON — это десятки
/// мегабайт текста и секунды на кодирование при каждом персисте; раньше
/// персист вызывался ВНУТРИ цикла по пачкам, то есть весь индекс
/// переписывался до шести раз за один поиск. Здесь — компактная запись
/// Float16 и один персист в конце.
actor SemanticIndex {
    static let shared = SemanticIndex()

    /// Блок файла с вектором.
    struct ChunkVec {
        let crumb: String        // «Файл → H1 → H2»
        let preview: String      // начало блока — сниппет для выдачи
        let vec: [Float]         // нормированный
    }

    private struct FileVecs {
        let mtime: Double
        let chunks: [ChunkVec]
    }

    private var index: [String: FileVecs] = [:]
    private var loaded = false
    private var indexing = false
    private var generation = 0                 // растёт в useForTests: осиротевший refresh умирает
    /// Сколько знаков блока держим для показа в выдаче.
    private static let previewChars = 700
    /// Кап фоновой доиндексации за вызов, В БЛОКАХ. Первый поиск на большом
    /// графе иначе ставит в очередь тысячи embed-вызовов в ту же Ollama, что
    /// обслуживает подсказки живой встречи. Хвост доедет со следующими
    /// поисками.
    /// Дефолт бережёт живую встречу: подсказки и индексация делят одну
    /// Ollama. Разовое построение индекса с нуля (4785 блоков рабочего графа)
    /// при этом растянулось бы на сотню поисков, поэтому лимит поднимается
    /// переменной окружения:
    ///   CHAROITE_INDEX_CHUNKS=5000 open -a Charoite
    private static var maxChunksPerRefresh: Int {
        if let raw = ProcessInfo.processInfo.environment["CHAROITE_INDEX_CHUNKS"],
           let n = Int(raw), n > 0 { return n }
        return 48
    }
    private static let batchSize = 8

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
        // Ждём смерти чужого refresh, но НЕ бесконечно: раньше здесь стоял
        // голый `while indexing { await Task.yield() }`, и если предыдущий
        // прогон оставлял флаг поднятым, следующий тест вставал намертво —
        // не падал с внятной ошибкой, а висел до таймаута CI. Генерация уже
        // увеличена, так что чужие записи всё равно отбрасываются: ждать
        // вечно незачем.
        for _ in 0..<1000 where indexing { await Task.yield() }
        indexing = false
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

    /// Сколько блоков в индексе у файла — для тестов чанкинга.
    func chunkCount(of path: String) -> Int {
        loadIfNeeded()
        return index[path]?.chunks.count ?? 0
    }
    #endif

    private var storeURL: URL {
        if let storeOverride { return storeOverride }
        let dir = FileManager.default.urls(for: .applicationSupportDirectory,
                                           in: .userDomainMask)[0]
            .appendingPathComponent("Charoite", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        // v2: формат сменился с «вектор на файл» на «векторы блоков».
        // Старый semantic_index.json просто перестаёт читаться — индекс
        // соберётся заново фоном, ломать нечего.
        return dir.appendingPathComponent("semantic_index_v2.bin")
    }

    private func embedTexts(_ texts: [String]) async -> [[Float]]? {
        if let embedOverride { return await embedOverride(texts) }
        return await Self.embed(texts)
    }

    /// Лучший блок каждого файла: (путь, cosine, сниппет блока).
    ///
    /// Сниппет отдаётся вместе со скором не для красоты: раньше семантический
    /// хит показывал НАЧАЛО файла, потому что искать в нём было нечего —
    /// лексических игл в семантическом попадании по определению нет. Для
    /// стенограммы это значило «нашли по смыслу в середине, показали
    /// приветствие».
    func similar(to query: String, within paths: Set<String>,
                 limit: Int) async -> [(path: String, score: Double, snippet: String)] {
        loadIfNeeded()
        guard !index.isEmpty,
              let qv = await embedTexts([query])?.first else { return [] }
        let q = Self.unit(qv)
        var best: [(path: String, score: Double, snippet: String)] = []
        for (path, entry) in index where paths.contains(path) {
            var top = 0.0
            var snippet = ""
            for chunk in entry.chunks {
                // Разная размерность = сменилась модель эмбеддингов. Считать
                // косинус по общему префиксу нельзя: получаются тихо
                // заниженные похожести и случайный порядок выдачи.
                guard chunk.vec.count == q.count else { continue }
                var s = 0.0
                for i in 0..<q.count { s += Double(q[i]) * Double(chunk.vec[i]) }
                if s > top { top = s; snippet = chunk.preview }
            }
            if top >= 0.35 { best.append((path, top, snippet)) }
        }
        best.sort { $0.score > $1.score }
        return Array(best.prefix(limit))
    }

    /// Сколько файлов в индексе — для строки статуса в Настройках.
    func count() -> Int {
        loadIfNeeded()
        return index.count
    }

    /// Сколько блоков всего — там же, чтобы видеть глубину индекса.
    func totalChunks() -> Int {
        loadIfNeeded()
        return index.values.reduce(0) { $0 + $1.chunks.count }
    }

    /// Лучшая похожесть — сигнал для гейта честности.
    func bestSimilarity(to query: String, within paths: Set<String>) async -> Double {
        await similar(to: query, within: paths, limit: 1).first?.score ?? 0
    }

    /// Фоновая доиндексация изменившихся файлов: режем на блоки, эмбеддим
    /// пачками, персист ОДИН раз в конце.
    func refresh(files: [(path: String, mtime: Double, text: String)]) async {
        loadIfNeeded()
        guard !indexing else { return }
        indexing = true
        defer { indexing = false }
        let gen = generation

        let pending = files.filter { index[$0.path]?.mtime != $0.mtime }
        guard !pending.isEmpty else { return }

        // Файл берём ЦЕЛИКОМ или не берём вовсе. Если резать его между
        // вызовами, недобранные блоки не доедут никогда: mtime сохраняется
        // только у полностью обработанного файла, а частичный результат
        // отбрасывается. Стенограмма на 74 блока при лимите 48 иначе просто
        // не попала бы в индекс — то есть ровно тот файл, ради которого всё
        // это и затевалось.
        var queue: [(path: String, mtime: Double, chunk: Chunker.Chunk)] = []
        for file in pending {
            let title = (file.path as NSString).deletingPathExtension
            let chunks = Chunker.chunks(of: file.text, title: title)
            guard !chunks.isEmpty else { continue }
            // Лимит проверяем ПЕРЕД добавлением, но первый файл берём всегда,
            // даже если он один больше лимита.
            if !queue.isEmpty, queue.count + chunks.count > Self.maxChunksPerRefresh { break }
            queue += chunks.map { (file.path, file.mtime, $0) }
        }
        guard !queue.isEmpty else { return }

        var built: [String: (mtime: Double, chunks: [ChunkVec])] = [:]
        let batches = stride(from: 0, to: queue.count, by: Self.batchSize).map {
            Array(queue[$0..<min($0 + Self.batchSize, queue.count)])
        }

        var completed = Set<String>()
        var started = Set<String>()
        for batch in batches {
            guard let embs = await embedTexts(batch.map(\.chunk.embeddingText)),
                  embs.count == batch.count else { break }   // Ollama лежит/нет модели
            // за await эмбеддера индекс могли подменить (useForTests) —
            // осиротевший снапшот не имеет права писать в чужой store
            guard gen == generation else { return }
            for (item, emb) in zip(batch, embs) {
                started.insert(item.path)
                built[item.path, default: (item.mtime, [])].chunks.append(
                    ChunkVec(crumb: item.chunk.breadcrumb,
                             preview: String(item.chunk.text.prefix(Self.previewChars)),
                             vec: Self.unit(emb)))
            }
        }
        // Файл записываем, только если проиндексированы ВСЕ его блоки: иначе
        // mtime сохранится при половине блоков, и оставшиеся не доедут уже
        // никогда — файл будет считаться свежим.
        let expected = Dictionary(grouping: queue, by: \.path).mapValues(\.count)
        for (path, entry) in built where entry.chunks.count == expected[path] {
            index[path] = FileVecs(mtime: entry.mtime, chunks: entry.chunks)
            completed.insert(path)
        }
        if !completed.isEmpty { persist() }
    }

    // MARK: - Хранение (бинарное)

    private func loadIfNeeded() {
        guard !loaded else { return }
        loaded = true
        guard let data = try? Data(contentsOf: storeURL) else { return }
        index = Self.decode(data) ?? [:]
    }

    private func persist() {
        let data = Self.encode(index)
        try? data.write(to: storeURL, options: .atomic)
    }

    private static let magic: [UInt8] = Array("CHV2".utf8)

    private static func encode(_ index: [String: FileVecs]) -> Data {
        var out = Data(magic)
        out.append(contentsOf: withBytes(UInt32(index.count)))
        for (path, entry) in index {
            appendString(&out, path)
            out.append(contentsOf: withBytes(entry.mtime.bitPattern))
            out.append(contentsOf: withBytes(UInt32(entry.chunks.count)))
            for chunk in entry.chunks {
                appendString(&out, chunk.crumb)
                appendString(&out, chunk.preview)
                out.append(contentsOf: withBytes(UInt32(chunk.vec.count)))
                // Float16: вдвое компактнее, а на нормированных векторах
                // потеря точности для косинуса в пятом знаке — шум.
                for v in chunk.vec {
                    out.append(contentsOf: withBytes(Float16(v).bitPattern))
                }
            }
        }
        return out
    }

    private static func decode(_ data: Data) -> [String: FileVecs]? {
        var cursor = 0
        func take(_ n: Int) -> Data? {
            guard cursor + n <= data.count else { return nil }
            defer { cursor += n }
            return data.subdata(in: (data.startIndex + cursor)..<(data.startIndex + cursor + n))
        }
        func uint32() -> UInt32? { take(4).map { $0.withUnsafeBytes { $0.loadUnaligned(as: UInt32.self) } } }
        func string() -> String? {
            guard let len = uint32(), let bytes = take(Int(len)) else { return nil }
            return String(data: bytes, encoding: .utf8)
        }
        guard let head = take(magic.count), Array(head) == magic, let files = uint32() else { return nil }
        var out: [String: FileVecs] = [:]
        for _ in 0..<files {
            guard let path = string(),
                  let mtimeBits = take(8)?.withUnsafeBytes({ $0.loadUnaligned(as: UInt64.self) }),
                  let chunkCount = uint32() else { return out }
            var chunks: [ChunkVec] = []
            for _ in 0..<chunkCount {
                guard let crumb = string(), let preview = string(), let dim = uint32(),
                      let raw = take(Int(dim) * 2) else { return out }
                var vec = [Float]()
                vec.reserveCapacity(Int(dim))
                raw.withUnsafeBytes { buf in
                    for i in 0..<Int(dim) {
                        vec.append(Float(Float16(bitPattern: buf.loadUnaligned(fromByteOffset: i * 2,
                                                                              as: UInt16.self))))
                    }
                }
                chunks.append(ChunkVec(crumb: crumb, preview: preview, vec: vec))
            }
            out[path] = FileVecs(mtime: Double(bitPattern: mtimeBits), chunks: chunks)
        }
        return out
    }

    private static func appendString(_ data: inout Data, _ s: String) {
        let bytes = Array(s.utf8)
        data.append(contentsOf: withBytes(UInt32(bytes.count)))
        data.append(contentsOf: bytes)
    }

    private static func withBytes<T>(_ value: T) -> [UInt8] {
        withUnsafeBytes(of: value) { Array($0) }
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
