import Foundation
import XCTest
@testable import CharoiteApp

/// Ход чата для проб и бенча памяти — тем же кодом, что в приложении (№632):
/// поиск с параметрами руки, основа промпта и блок графа из `ChatRetrieval`,
/// то же тело запроса без стрима и с температурой 0.
///
/// Расходится с чатом намеренно, и отчёт пишет движок и руку:
/// - движок — локальный Swift-поиск по папке графа (`localSearchOutcome`);
///   чат с поднятым brain-сервером (:8100) идёт через него, этот путь бенч
///   не меряет (№743);
/// - одна реплика вместо истории, без стрима, температура 0;
/// - модель зафиксирована: подмены на лёгкую во время встречи нет.
enum ProbeTurn {
    /// Модель синтеза зафиксирована: бенч сравнивает правки, а не модели.
    static let model = "qwen3.6:35b-a3b"
    static let engine = "local (brain обойдён)"

    /// Рука бенча: `CHAROITE_BENCH_ARM=legacy` — прежние 5×1200 с бюджетом
    /// поиска по умолчанию, иначе — параметры чата.
    static var arm: (name: String, params: ChatRetrieval.Params) {
        ProcessInfo.processInfo.environment["CHAROITE_BENCH_ARM"] == "legacy"
            ? ("legacy", .legacyProbe) : ("chat", .chat)
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

    /// Поиск фоном зовёт `SemanticIndex.refreshInBackground(pruneMissing: true)` по графу
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
        let local = inferenceIsLocal
        let system = ChatRetrieval.baseSystem(inferenceIsLocal: local)
            + ChatRetrieval.contextBlock(outcome).block
        guard let url = URL(string: AppSettings.ollamaURL + "/api/chat") else { return nil }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 600
        req.httpBody = try? JSONSerialization.data(withJSONObject: ChatRetrieval.requestBody(
            model: model,
            messages: [["role": "system", "content": system], ["role": "user", "content": question]],
            stream: false, temperature: 0))
        // Прокси обнуляем, как в проде: системный прокси ломает запрос к localhost.
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]
        guard let (data, _) = try? await URLSession(configuration: cfg).data(for: req),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let msg = obj["message"] as? [String: Any],
              let text = msg["content"] as? String, !text.isEmpty else { return nil }
        return text
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
    static func header(_ title: String) -> String {
        let (name, p) = arm
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
