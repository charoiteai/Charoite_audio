import Foundation
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

    static func search(_ query: String, params: ChatRetrieval.Params,
                       root: URL) async -> ArchiveSearch.Outcome {
        await ArchiveSearch.localSearchOutcome(
            query: ChatRetrieval.query(from: [.init(role: "user", text: query)]), limit: params.limit, snippet: params.snippet, budget: params.budget,
            root: root, includeLinked: inferenceIsLocal)
    }

    /// Ответ модели или nil — модель не ответила (бенч считает такой вопрос
    /// с нулём фактов и падает своим сообщением).
    static func answer(question: String, vault: String) async -> String? {
        let local = inferenceIsLocal
        let system = ChatRetrieval.baseSystem(inferenceIsLocal: local)
            + ChatRetrieval.contextBlock(vault: vault).block
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

    /// Каталог отчётов — свой у каждой учётки (`NSTemporaryDirectory()`,
    /// `/var/folders/…/T`), а не общий `/tmp`. На Darwin его отдаёт система, а
    /// не `TMPDIR` (опыт out4: `TMPDIR=/private/tmp` — отчёт всё равно в
    /// `/var/folders`); проверка — страховка: не симлинк, владелец — эта
    /// учётка, без записи группе и прочим. nil — писать некуда, вызывающий падает.
    static func reportPath(_ name: String) -> String? {
        var dir = NSTemporaryDirectory()
        while dir.count > 1, dir.hasSuffix("/") { dir.removeLast() }
        var st = stat()
        guard lstat(dir, &st) == 0, st.st_mode & S_IFMT == S_IFDIR, st.st_uid == getuid(),
              st.st_mode & (S_IWGRP | S_IWOTH) == 0 else { return nil }
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
