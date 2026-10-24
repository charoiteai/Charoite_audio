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
/// молча режет вход bge-m3 до 2048 токенов — 5–6 тысяч знаков (замер 26.09:
/// хвост после предела вектор не меняет), так что «просто поднять лимит» было
/// не вариантом.
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
    /// Единственный писатель индекса и файла. Фоновую доиндексацию раньше
    /// ставил поиск своим `Task.detached` без ручки, а актор узнавал о ней,
    /// только когда она в него входила: поздняя задача прошлого теста брала
    /// поколение следующего и прунила его индекс снимком чужого графа, а тест
    /// ждал запись опросом по часам (№534, №412). Теперь задача — собственность
    /// экземпляра: её можно дождаться и закрыть.
    private var inFlight: Task<Void, Never>?
    private var closed = false
    /// Порядок обходов графа (№768). Номер выдаёт актор, а не часы вызывающего:
    /// снимок медленного старого обхода не прунит то, что записано по новому.
    private var walkSeq: UInt64 = 0
    /// Номер обхода последнего применённого prune и его ключи: задача старшего
    /// обхода не записывает файл, которого в нём уже нет (удалили, пока она
    /// ждала эмбеддер).
    private var prunedWalk: UInt64 = 0
    private var prunedAlive = Alive(seen: [])
    /// Индекс в памяти расходится с файлом. Пишет его только задача-писатель:
    /// prune при занятой ячейке удаляет в памяти и не ставит запись десятков
    /// мегабайт на путь ответа поиска.
    private var dirty = false
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

    // Файл и эмбеддер экземпляр получает при рождении и больше не меняет.
    // Раньше тесты подменяли их у общего `shared` (useForTests), и хвост
    // одного теста писал в сессию другого; теперь тест и проба строят свой
    // экземпляр, а `shared` знает только приложение. nil — правило
    // `storeURL` / настоящая Ollama.
    private let storeOverride: URL?
    private let embedOverride: (([String]) async -> [[Float]]?)?

    private init() {
        storeOverride = nil
        embedOverride = nil
    }

    #if DEBUG
    /// Экземпляр тестов и проб. `embedder: nil` — настоящий эмбеддер (живые
    /// пробы); `loadStore` — начать с содержимого `store`, а не с пустого индекса.
    init(store: URL, loadStore: Bool = false,
         embedder: (([String]) async -> [[Float]]?)?) {
        storeOverride = store
        embedOverride = embedder
        loaded = !loadStore    // true — не подтягивать файл с диска
    }

    /// Страховка: тест, всё же тронувший `shared`, пишет в этот файл процесса,
    /// а не в индекс приложения. Тесты и пробы строят свои экземпляры (№534).
    static let unclaimedTestStore = FileManager.default.temporaryDirectory
        .appendingPathComponent("charoite_unclaimed_index_\(UUID().uuidString).bin")

    /// `close()` начался: флаг ставится в одном синхронном отрезке с отменой
    /// писателя — увидевший `true` знает, что отмена уже запрошена.
    var isClosedForTests: Bool { closed }

    /// Дождаться фоновой доиндексации: тест проверяет её результат.
    func drainForTests() async {
        while let running = inFlight { await running.value }
    }

    /// Конец жизни тестового экземпляра — до удаления его файла: идущая задача
    /// отменяется (запрос к Ollama обрывается) и дожидается, после чего ни
    /// она, ни новая не пишут. Без этого хвост persist воссоздавал удалённую
    /// копию индекса с превью встреч (раньше это делал endTestSession).
    func close() async {
        closed = true
        while let running = inFlight {
            running.cancel()
            await running.value
        }
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

    /// Файл индекса приложения — один на учётку.
    /// v2: формат сменился с «вектор на файл» на «векторы блоков».
    /// Старый semantic_index.json просто перестаёт читаться — индекс
    /// соберётся заново фоном, ломать нечего.
    static var liveStoreURL: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Charoite", isDirectory: true)
            .appendingPathComponent("semantic_index_v2.bin")
    }

    private var storeURL: URL {
        if let storeOverride { return storeOverride }
        #if DEBUG
        // Под XCTest `shared` — не файл приложения: тест демо-графа через
        // localSearch прунил живой индекс до своей фикстуры (№740, опыт 18.10:
        // 141 КБ → 8 байт за один прогон ArchiveLinksTests).
        if NSClassFromString("XCTestCase") != nil { return Self.unclaimedTestStore }
        #endif
        let url = Self.liveStoreURL
        FileManager.default.createPrivateDirectory(at: url.deletingLastPathComponent())  // индекс хранит текст встреч
        return url
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
        await similarChecked(to: query, within: paths, limit: limit)?.hits ?? []
    }

    /// Что семантика проверила: попадания и сколько файлов из `within`
    /// реально сравнено с запросом (есть блок с вектором той же модели).
    struct Checked {
        let hits: [(path: String, score: Double, snippet: String)]
        let compared: Int
    }

    /// Как `similar`, но nil — семантика не отработала (индекс пуст или
    /// эмбеддер не ответил); `compared` — охват: векторы другой модели и
    /// непроиндексированные файлы не сравнивались. Поиску чата нужно
    /// различать «не нашла» и «не искала» (№634).
    func similarChecked(to query: String, within paths: Set<String>,
                        limit: Int) async -> Checked? {
        loadIfNeeded()
        guard !index.isEmpty,
              let qv = await embedTexts([query])?.first else { return nil }
        let q = Self.unit(qv)
        var best: [(path: String, score: Double, snippet: String)] = []
        var compared = 0
        for (path, entry) in index where paths.contains(path) {
            var top = 0.0
            var snippet = ""
            var any = false
            for chunk in entry.chunks {
                // Разная размерность = сменилась модель эмбеддингов. Считать
                // косинус по общему префиксу нельзя: получаются тихо
                // заниженные похожести и случайный порядок выдачи.
                guard chunk.vec.count == q.count else { continue }
                any = true
                var s = 0.0
                for i in 0..<q.count { s += Double(q[i]) * Double(chunk.vec[i]) }
                if s > top { top = s; snippet = chunk.preview }
            }
            if any { compared += 1 }
            if top >= 0.35 { best.append((path, top, snippet)) }
        }
        best.sort { $0.score > $1.score }
        return Checked(hits: Array(best.prefix(limit)), compared: compared)
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

    /// Что живо в графе по одному обходу поиска: ключи, которые обход увидел
    /// (прочитанные, непрочитанные — iCloud выгрузил, нет прав, не UTF-8 — и
    /// дубли), и поддеревья, которые перечислить не удалось: под ними жив любой
    /// ключ. Прунить «по списку прочитанного» нельзя: файл на диске, который не
    /// открылся, терял запись и уходил на переэмбеддинг в общую Ollama, а
    /// запертая подпапка — целиком (№768). Одна запертая папка не выключает
    /// prune остального графа: забытая встреча рядом всё равно уходит.
    struct Alive {
        let seen: Set<String>
        let unscanned: [String]
        init(seen: Set<String>, unscanned: [String] = []) {
            self.seen = seen
            self.unscanned = unscanned
        }
        func contains(_ key: String) -> Bool {
            seen.contains(key) || unscanned.contains { key == $0 || key.hasPrefix($0 + "/") }
        }
    }

    /// `alive: nil` — о графе ничего не известно (корень не перечислен,
    /// частичный снимок теста): prune нет.
    struct Walk {
        let seq: UInt64
        let alive: Alive?
    }

    /// Номер обхода — до перечисления каталога.
    func beginWalk() -> UInt64 {
        walkSeq &+= 1
        return walkSeq
    }

    /// Фоновая доиндексация изменившихся файлов: режем на блоки, эмбеддим
    /// пачками, персист ОДИН раз в конце.
    ///
    /// Prune — выбросить из индекса ключи, которых нет в `alive` обхода.
    /// Индекс хранит по 700 знаков превью на каждый блок каждого файла,
    /// включая стенограммы, а удаления не было вовсе: `forget_meeting`
    /// вычищал транскрипт, архив, граф и даже снимки облачных ревизий — а
    /// текст «забытой» встречи продолжал жить в
    /// `Application Support/Charoite/semantic_index_v2.bin` (аудит 16.08).
    ///
    /// Прямой вызов (тесты, проба BuildRealIndex) дожидается идущей фоновой
    /// задачи и делает свой проход: после возврата его файлы прошли один
    /// проход доиндексации — с тем же капом блоков и с остановкой на отказе
    /// эмбеддера, что и фоновый. Отмена вызывающего отменяет и проход.
    /// `alive: nil` — без prune. `false` — экземпляр закрыт, прохода не было.
    /// Продукт зовёт только `refreshInBackground`.
    #if DEBUG
    @discardableResult
    func refresh(files: [(path: String, mtime: Double, text: String)],
                 alive: Set<String>? = nil) async -> Bool {
        while let running = inFlight { await running.value }
        guard let task = start(files: files, walk: Walk(seq: beginWalk(), alive: alive.map { Alive(seen: $0) }),
                               priority: Task.currentPriority) else { return false }
        await withTaskCancellationHandler { await task.value } onCancel: { task.cancel() }
        return true
    }
    #endif

    /// Доиндексация после поиска: ответ её не ждёт. Идущая задача не
    /// дублируется — эмбеддинг этого снимка пропускается (`false`), хвост
    /// доедет со следующим (дозировка общей с подсказками Ollama). А prune
    /// снимка применяется сразу, в памяти: раньше он пропускался вместе со
    /// снимком, и забытая во время доиндексации встреча жила в индексе до
    /// следующего поиска (№768). Принятая задача регистрируется до возврата —
    /// тест может её дождаться.
    @discardableResult
    func refreshInBackground(files: [(path: String, mtime: Double, text: String)],
                             walk: Walk) -> Bool {
        guard !closed else { return false }
        if inFlight != nil {
            prune(walk)
            return false
        }
        return start(files: files, walk: walk, priority: .background) != nil
    }

    /// Единственная точка старта писателя: занятая ячейка или закрытый
    /// экземпляр — задачи нет. Один писатель держит она, а не договорённость
    /// вызывающих (out1, DS К1).
    private func start(files: [(path: String, mtime: Double, text: String)],
                       walk: Walk, priority: TaskPriority) -> Task<Void, Never>? {
        guard !closed, inFlight == nil else { return nil }
        let task = Task.detached(priority: priority) { [self] in
            await self.run(files: files, walk: walk)
        }
        inFlight = task
        return task
    }

    /// Писать вправе только живая задача незакрытого экземпляра.
    private var mayWrite: Bool { !closed && !Task.isCancelled }

    /// Единственная точка удаления записей. Загрузка — здесь же: prune из
    /// занятой ветки на ещё не загруженном индексе стирал бы пустоту, а
    /// задача потом подняла бы удалённый файл с диска. Обход старше
    /// применённого не прунит: его ключи могли устареть.
    private func prune(_ walk: Walk) {
        guard let alive = walk.alive, walk.seq > prunedWalk else { return }
        loadIfNeeded()
        prunedWalk = walk.seq
        prunedAlive = alive
        let gone = index.keys.filter { !alive.contains($0) }
        for path in gone { index.removeValue(forKey: path) }
        if !gone.isEmpty { dirty = true }
    }

    private func run(files: [(path: String, mtime: Double, text: String)], walk: Walk) async {
        // ячейка занята только этой задачей — `start` второго не ставит
        defer { inFlight = nil }
        guard mayWrite else { return }   // отменена до первого шага
        loadIfNeeded()
        prune(walk)
        // Закрытый экземпляр и отменённая задача не пишут: иначе хвост persist
        // воссоздал бы удалённую копию индекса с превью встреч.
        // Флаг снимает только удачная запись: иначе удаление, не дошедшее до
        // файла, больше никто не повторил бы (out1 DS I1).
        // Удаление — на диск сразу, не после эмбеддера: выход или зависшая
        // Ollama оставили бы превью забытой встречи в файле (out2 Sonnet I2).
        if dirty, mayWrite { dirty = !persist() }
        await build(files: files, walk: walk)
        if dirty, mayWrite { dirty = !persist() }
    }

    private func build(files: [(path: String, mtime: Double, text: String)], walk: Walk) async {
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

        for batch in batches {
            guard let embs = await embedTexts(batch.map(\.chunk.embeddingText)),
                  embs.count == batch.count else { break }   // Ollama лежит/нет модели
            // за await эмбеддера экземпляр могли закрыть — запись после уборки
            // воссоздала бы удалённый файл
            guard mayWrite else { return }
            for (item, emb) in zip(batch, embs) {
                built[item.path, default: (item.mtime, [])].chunks.append(
                    ChunkVec(crumb: item.chunk.breadcrumb,
                             preview: String(item.chunk.text.prefix(Self.previewChars)),
                             vec: Self.unit(emb)))
            }
        }
        // Ранний выход по отказу эмбеддера минует проверку в цикле, а отмена
        // обрывает запрос как отказ — готовые батчи закрытого экземпляра не пишем.
        guard mayWrite else { return }
        // Файл записываем, только если проиндексированы ВСЕ его блоки: иначе
        // mtime сохранится при половине блоков, и оставшиеся не доедут уже
        // никогда — файл будет считаться свежим.
        let expected = Dictionary(grouping: queue, by: \.path).mapValues(\.count)
        // Пока задача ждала эмбеддер, применён prune более нового обхода:
        // файла вне его ключей уже нет — запись воскресила бы удалённое.
        let stale = prunedWalk > walk.seq
        for (path, entry) in built where entry.chunks.count == expected[path] {
            if stale && !prunedAlive.contains(path) { continue }
            index[path] = FileVecs(mtime: entry.mtime, chunks: entry.chunks)
            dirty = true
        }
    }

    // MARK: - Хранение (бинарное)

    private func loadIfNeeded() {
        guard !loaded else { return }
        loaded = true
        guard let data = try? Data(contentsOf: storeURL) else { return }
        index = Self.decode(data) ?? [:]
    }

    private func persist() -> Bool {
        let data = Self.encode(index)
        do {
            try data.write(to: storeURL, options: .atomic)
            return true
        } catch {
            return false
        }
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

    #if DEBUG
    /// mtime записей в файле на диске — проба судит по файлу, а не по памяти
    /// актора: открытое приложение переписывает файл своим снимком (out10 №632).
    static func storedMtimes(at url: URL) -> [String: Double]? {
        (try? Data(contentsOf: url)).flatMap(decode)?.mapValues(\.mtime)
    }
    #endif

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
