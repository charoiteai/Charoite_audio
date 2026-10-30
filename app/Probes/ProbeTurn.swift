import Foundation
import XCTest
@testable import CharoiteApp

/// Ход чата для проб и бенча памяти — тем же кодом, что в приложении (№632):
/// поиск с параметрами руки, основа промпта и блок графа из `ChatRetrieval`,
/// то же тело запроса без стрима и с температурой 0.
///
/// Движок — тот же локальный Swift-поиск по папке графа, что у чата
/// (`localSearchOutcome`; сервер :8100 чат не читает с №754). Расходится с
/// чатом намеренно, и отчёт пишет руку:
/// - одна реплика вместо истории, без стрима, температура 0;
/// - модель зафиксирована: подмены на лёгкую во время встречи нет.
enum ProbeTurn {
    /// Модель синтеза зафиксирована: бенч сравнивает правки, а не модели.
    static let model = "qwen3.6:35b-a3b"
    static let engine = "local"

    /// Рука бенча из `CHAROITE_BENCH_ARM`: пусто или `chat` — параметры чата,
    /// `legacy` — прежние 5×1200 с бюджетом поиска по умолчанию, `p<N>` —
    /// N кусков по 1000 знаков с бюджетом N×1000 (число кусков на деле задаёт
    /// бюджет, отчёт пишет их счёт; кусок с шапкой длиннее 1000, и последний
    /// упаковщик режет, а p1–p2 упираются в потолок источника 600/800 —
    /// с p3+ не сравнимы, n752-out1 Sonnet I3). Прочее — ошибка: опечатка `p4l` молча
    /// перезаписывала бы отчёт руки `chat` (n752-in1, Sonnet I5, DS M5).
    static func arm() throws -> (name: String, params: ChatRetrieval.Params) {
        let raw = (ProcessInfo.processInfo.environment["CHAROITE_BENCH_ARM"] ?? "")
            .trimmingCharacters(in: .whitespaces)
        switch raw {
        case "", "chat": return ("chat", .chat)
        case "legacy": return ("legacy", .legacyProbe)
        default:
            guard raw.range(of: #"^p[1-9][0-9]{0,2}$"#, options: .regularExpression) != nil,
                  let n = Int(raw.dropFirst()) else {
                throw SetupError(description: "CHAROITE_BENCH_ARM=\(raw): ждём chat, legacy или p<N> (1–999)")
            }
            return (raw, ChatRetrieval.Params(limit: n, snippet: 1000, budget: n * 1000))
        }
    }

    static var inferenceIsLocal: Bool {
        MemoryScreenPolicy.inferenceIsLocal(base: AppSettings.ollamaURL)
    }

    /// Индекс пробы. Построить его может только `isolateIndex` — поиск пробы
    /// без изолированного индекса не собирается (раньше это проверял флаг
    /// подмены `shared` во время работы, №534).
    struct Index {
        let index: SemanticIndex
        fileprivate init(_ index: SemanticIndex) { self.index = index }
    }

    /// Поиск фоном зовёт `SemanticIndex.refreshInBackground` с prune по графу
    /// пробы: на индексе приложения проба на чужом графе стирает его
    /// (18.10: синтетика из трёх встреч оставила в нём одну). Поэтому поиск
    /// пробы идёт только по индексу из `isolateIndex`.
    static func search(_ query: String, params: ChatRetrieval.Params,
                       root: URL, index: Index) async -> ArchiveSearch.Outcome {
        // под окно хода, как в чате: бенч мерит то, что получит модель (№743)
        ChatRetrieval.fit(await ArchiveSearch.localSearchOutcome(
            query: ChatRetrieval.query(from: [.init(role: "user", text: query)]), limit: params.limit, snippet: params.snippet, budget: params.budget,
            root: root, includeLinked: inferenceIsLocal, semanticIndex: index.index),
            system: ChatRetrieval.baseSystem(inferenceIsLocal: inferenceIsLocal), prompt: query)
    }

    /// Ответ модели или nil — модель не ответила (бенч считает такой вопрос
    /// с нулём фактов и падает своим сообщением).
    static func answer(question: String, outcome: ArchiveSearch.Outcome) async -> String? {
        guard let body = body(question: question, outcome: outcome) else { return nil }
        return await send(body)?.text
    }

    /// Тело запроса хода — JSON с упорядоченными ключами: два тела равны,
    /// только если равны запросы (повтор пачки сверяет его, n752-in1, DS I4).
    static func body(question: String, outcome: ArchiveSearch.Outcome) -> Data? {
        let prompt = system(question: question, outcome: outcome)
        return try? JSONSerialization.data(withJSONObject: ChatRetrieval.requestBody(
            model: model,
            messages: [["role": "system", "content": prompt], ["role": "user", "content": question]],
            stream: false, temperature: 0), options: [.sortedKeys])
    }

    /// Системный текст хода: основа и блок графа после окна. Окно — и здесь:
    /// повтор пачки после правки `Window` не пошлёт ход, которого чат не
    /// сделал бы (n752-out1, DS M7); на выдаче из `search` это тождество.
    static func system(question: String, outcome: ArchiveSearch.Outcome) -> String {
        let base = ChatRetrieval.baseSystem(inferenceIsLocal: inferenceIsLocal)
        return base + ChatRetrieval.contextBlock(ChatRetrieval.fit(outcome, system: base, prompt: question)).block
    }

    /// Сервер модели без пути и учётных данных — для сверки пачки с получателем
    /// (n752-out1, Sonnet I5, DS I1).
    static var server: String {
        guard let c = URLComponents(string: AppSettings.ollamaURL) else { return "?" }
        return "\(c.scheme ?? "?")://\(c.host ?? "?")" + (c.port.map { ":\($0)" } ?? "")
    }

    /// Ответ и счёт токенов промпта у Ollama: оценка окна — верхняя, отчёт
    /// пишет, сколько ушло на деле (n752-in1, DS C2).
    struct Reply { let text: String; let promptTokens: Int? }

    static func send(_ body: Data) async -> Reply? {
        guard let url = URL(string: AppSettings.ollamaURL + "/api/chat") else { return nil }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 600
        req.httpBody = body
        // Прокси обнуляем, как в проде: системный прокси ломает запрос к localhost.
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]
        guard let (data, _) = try? await URLSession(configuration: cfg).data(for: req),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let msg = obj["message"] as? [String: Any],
              let text = msg["content"] as? String, !text.isEmpty else { return nil }
        return Reply(text: text, promptTokens: obj["prompt_eval_count"] as? Int)
    }

    /// Пачка хода — выдача руки после окна и всё, чем отчёт судит ответ.
    /// Повтор (`CHAROITE_BENCH_REPLAY`) шлёт её через тот же `body`/`send`:
    /// правка промпта или блока графа видна в повторе, а поле `body` говорит,
    /// тот ли это запрос, что при записи (n752-in1, H C3, DS I3).
    struct Packet: Codable {
        struct Piece: Codable { let text: String; let rel: String?; let rank: Int }
        let arm: String
        let limit: Int, snippet: Int, budget: Int
        let model: String
        /// соседа по ссылке берут только при локальной модели (№695): повтор
        /// на другой хост отказывает (n752-in1, Sonnet I4)
        let local: Bool
        let server: String
        let q: String
        /// судья — тот, что при записи: правка набора не переписывает «при записи» (DS I2)
        let must: [String]
        let abstain: Bool
        let verdict: String
        let text: String
        let rels: [String]?
        let note: String
        let parts: [Piece]?
        let body: String?
        let answer: String?

        /// `body` nil — модель не спрашивали (прогон без синтеза): такую пачку
        /// повтор не берёт (DS I3)
        init(arm: (name: String, params: ChatRetrieval.Params), q: String, must: [String], abstain: Bool,
             outcome: ArchiveSearch.Outcome, body: Data?, answer: String?) {
            self.arm = arm.name
            limit = arm.params.limit
            snippet = arm.params.snippet
            budget = arm.params.budget
            model = ProbeTurn.model
            local = ProbeTurn.inferenceIsLocal
            server = ProbeTurn.server
            self.q = q
            self.must = must
            self.abstain = abstain
            verdict = outcome.verdict.rawValue
            text = outcome.text
            rels = outcome.rels
            note = outcome.note
            parts = outcome.parts?.map { Piece(text: $0.text, rel: $0.rel, rank: $0.rank) }
            self.body = body.map { String(decoding: $0, as: UTF8.self) }
            self.answer = answer
        }

        func outcome() throws -> ArchiveSearch.Outcome {
            guard let v = ArchiveSearch.Verdict(rawValue: verdict) else {
                throw SetupError(description: "пачка: вердикт \(verdict) не известен — «\(q)»")
            }
            return ArchiveSearch.Outcome(text: text, rels: rels, verdict: v, note: note,
                                         parts: parts?.map { ArchiveSearch.Part(text: $0.text, rel: $0.rel, rank: $0.rank) })
        }
    }

    /// Пачки пишутся по ходу, строка JSON на вопрос: прогон, упавший на
    /// 30-м вопросе, оставляет 29 пачек (n752-in1, Sonnet I6). Файл — 0600
    /// в проверенном каталоге отчётов, своё имя на каждый прогон: упавший
    /// перезапуск не стирает эталон (n752-out1, Sonnet I4).
    final class PacketLog {
        let path: String
        private let handle: FileHandle

        init(_ name: String) throws {
            let stamp = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "")
            path = try reportPath("\(name)_\(stamp)")
            guard writePrivate("", to: path), let h = FileHandle(forWritingAtPath: path) else {
                throw SetupError(description: "пачки не открылись: \(path)")
            }
            handle = h
        }

        deinit { try? handle.close() }

        func append(_ p: Packet) throws {
            var line = try JSONEncoder().encode(p)
            line.append(0x0A)
            try handle.write(contentsOf: line)
        }

        /// Оборванная последняя строка (прогон убит посреди записи) — пропуск
        /// с пометкой, а не отказ всему файлу; кривая строка в середине — отказ
        /// (n752-out1, Sonnet I7).
        static func read(_ raw: String) throws -> (packets: [Packet], torn: Bool) {
            let path = (raw as NSString).expandingTildeInPath
            guard let data = FileManager.default.contents(atPath: path) else {
                throw SetupError(description: "пачек нет: \(path)")
            }
            let lines = data.split(separator: 0x0A)
            var out: [Packet] = []
            for (i, line) in lines.enumerated() {
                do { out.append(try JSONDecoder().decode(Packet.self, from: Data(line))) } catch {
                    guard i == lines.count - 1 else {
                        throw SetupError(description: "пачка \(i + 1) не разобралась: \(path)")
                    }
                    return (out, true)
                }
            }
            return (out, false)
        }
    }

    /// Ранг факта в выдаче — наименьший `Part.rank` куска, где факт стоит во
    /// фрагменте, а не в шапке «• путь» или «Связано с» (имя файла несёт дату
    /// и тему встречи, n752-in1, Sonnet I2). −1 — слот гейта. Ранг — порядок
    /// упаковки, не позиция поиска (Sonnet I1): кривая по рукам — только из
    /// настоящих прогонов этих рук.
    /// Тело — текст без первой строки и без второй «Связано с» у соседа по
    /// ссылке; факт, разорванный переводом строки, ловит `norm` судьи,
    /// схлопывающий пробелы (n752-out1, Sonnet I6 и Minor).
    static func factRank(_ fact: String, in parts: [ArchiveSearch.Part]?,
                         norm: (String) -> String) -> Int? {
        let needle = norm(fact)
        return (parts ?? []).filter { part in
            var lines = part.text.split(separator: "\n", omittingEmptySubsequences: false).dropFirst()
            if lines.first?.trimmingCharacters(in: .whitespaces).hasPrefix("Связано с [[") == true {
                lines = lines.dropFirst()
            }
            return norm(lines.joined(separator: "\n")).contains(needle)
        }.map(\.rank).min()
    }

    /// Отказ подготовки пробы: тест бросает его и падает с причиной до работы.
    struct SetupError: Error, CustomStringConvertible { let description: String }

    /// Флаг пробы из окружения: пусто или 0 — нет, 1 — да; прочее — ошибка,
    /// а не тихий пропуск (`=true` не должен значить «не запрошено»).
    static func flag(_ name: String) throws -> Bool {
        let v = (ProcessInfo.processInfo.environment[name] ?? "")
            .trimmingCharacters(in: .whitespaces).lowercased()
        switch v {
        case "", "0", "false", "no": return false
        case "1", "true", "yes": return true
        default: throw SetupError(description: "\(name)=\(v): ждём 1 или 0")
        }
    }

    /// Индекс пробы — копия файла приложения во временном каталоге, эмбеддер
    /// настоящий: семантика как в чате, запись и прунинг — только в копию.
    static func isolateIndex(_ test: XCTestCase) async throws -> Index {
        let dir = (try reportPath("index") as NSString).deletingLastPathComponent
        let copy = URL(fileURLWithPath: dir)
            .appendingPathComponent("charoite_probe_index_\(UUID().uuidString).bin")
        // уборка — до копии: упавший setAttributes не оставит копию с превью встреч
        test.addTeardownBlock { try? FileManager.default.removeItem(at: copy) }
        let live = SemanticIndex.liveStoreURL
        if FileManager.default.fileExists(atPath: live.path) {
            try FileManager.default.copyItem(at: live, to: copy)
            // копия хранит превью встреч — права не по файлу приложения
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: copy.path)
        }
        // экземпляр — после копии: loadStore читает уже готовый файл
        let index = SemanticIndex(store: copy, loadStore: true, embedder: nil)
        // teardown-блоки идут в обратном порядке: индекс закрыт до уборки копии,
        // хвостовой persist её не воссоздаст
        test.addTeardownBlock { await index.close() }
        return Index(index)
    }

    /// Папка графа из окружения — абсолютный путь к существующему каталогу,
    /// иначе проба падает, а не зеленеет на пустом поиске (круги 4–5). Пустое
    /// значение `URL(fileURLWithPath:)` превратил бы в текущий каталог, а
    /// относительный путь приложение разрешает от корня данных, не от cwd (круг 6).
    static func graphDir(_ raw: String?) throws -> URL {
        let raw = raw?.trimmingCharacters(in: .whitespaces) ?? ""
        guard !raw.isEmpty else { throw SetupError(description: "CHAROITE_GRAPH_DIR не задан") }
        let path = (raw as NSString).expandingTildeInPath
        guard path.hasPrefix("/") else {
            throw SetupError(description: "CHAROITE_GRAPH_DIR=\(raw) — относительный путь; нужен абсолютный или с ~")
        }
        let url = URL(fileURLWithPath: path)
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir), isDir.boolValue else {
            throw SetupError(description: "папки графа нет: \(url.path) — проверь CHAROITE_GRAPH_DIR")
        }
        // канонический путь: ключи индекса — от него, как в ArchiveSearch (симлинк в корне, /tmp)
        return url.resolvingSymlinksInPath()
    }

    /// Каталог отчётов — свой у каждой учётки (`NSTemporaryDirectory()`,
    /// `/var/folders/…/T`), а не общий `/tmp`. На Darwin его отдаёт система, а
    /// не `TMPDIR` (опыт out4: `TMPDIR=/private/tmp` — отчёт всё равно в
    /// `/var/folders`); проверка — страховка: не симлинк, владелец — эта
    /// учётка, без записи группе и прочим. Зовётся до работы пробы (круг 5).
    static func reportPath(_ name: String) throws -> String {
        var dir = NSTemporaryDirectory()
        while dir.count > 1, dir.hasSuffix("/") { dir.removeLast() }
        var st = stat()
        let why: String?
        if lstat(dir, &st) != 0 { why = "lstat: \(String(cString: strerror(errno)))" }
        else if st.st_mode & S_IFMT != S_IFDIR { why = "не каталог (симлинк?)" }
        else if st.st_uid != getuid() { why = "владелец uid \(st.st_uid), а не \(getuid())" }
        else if st.st_mode & (S_IWGRP | S_IWOTH) != 0 { why = "запись группе/прочим, режим \(String(st.st_mode & 0o7777, radix: 8))" }
        else { why = nil }
        if let why { throw SetupError(description: "временный каталог \(dir) не годится для отчёта: \(why)") }
        return (dir as NSString).appendingPathComponent("charoite_\(name).txt")
    }

    /// Отчёт пробы несёт сниппеты встреч и ответы модели — только владельцу
    /// (0600). `createFile` ставит новый файл на место прежнего (опыт out3:
    /// новый inode и 0600 поверх 0644; каталог без записи — false, прежний
    /// файл не тронут). false — нового отчёта нет, прежний мог остаться:
    /// вызывающий падает и дальше на этот путь не ссылается.
    static func writePrivate(_ text: String, to path: String) -> Bool {
        FileManager.default.createFile(atPath: path, contents: Data(text.utf8),
                                       attributes: [.posixPermissions: PrivateFiles.fileMode])
    }

    /// Шапка отчёта: рука, параметры, движок и модель — чтобы два отчёта
    /// нельзя было сравнить, не видя, чем они отличаются.
    static func header(_ title: String, arm: (name: String, params: ChatRetrieval.Params)) -> String {
        let (name, p) = arm
        // для pN это заказ, а не факт: длины кусков — в строке «ранги» (n752-out1, Haiku C1)
        return "\(title)\nрука: \(name) (limit \(p.limit), snippet \(p.snippet), budget \(p.budget)); "
            + "движок: \(engine); соседи по ссылке: \(inferenceIsLocal ? "да" : "нет"); модель: \(model)\n"
    }
}

extension ChatRetrieval.Params {
    /// Прежние параметры поиска проб: 5×1200, бюджет по умолчанию — только
    /// для сравнения рук в бенче, в продукте их нет. Прежние только в поиске:
    /// промпт, `num_predict` и температура — общие с рукой чата.
    static let legacyProbe = ChatRetrieval.Params(limit: 5, snippet: 1200,
                                                  budget: ArchiveSearch.defaultBudget)
}
