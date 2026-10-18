import Foundation
@testable import CharoiteApp

/// Ход чата для проб и бенча памяти — тем же кодом, что в приложении (№632):
/// поиск с параметрами руки, основа промпта и блок графа из `ChatRetrieval`,
/// то же тело запроса без стрима и с температурой 0.
///
/// Движок — локальный Swift-поиск по папке графа (`localSearchOutcome`):
/// brain-сервер (:8100) пробы обходят нарочно, и отчёт это пишет.
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
            query: query, limit: params.limit, snippet: params.snippet, budget: params.budget,
            root: root, includeLinked: inferenceIsLocal)
    }

    /// Ответ модели или nil — модель не ответила (такой вопрос вне счёта).
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

    /// Шапка отчёта: рука, параметры, движок и модель — чтобы два отчёта
    /// нельзя было сравнить, не видя, чем они отличаются.
    static func header(_ title: String) -> String {
        let (name, p) = arm
        return "\(title)\nрука: \(name) (limit \(p.limit), snippet \(p.snippet), budget \(p.budget)); "
            + "движок: \(engine); соседи по ссылке: \(inferenceIsLocal ? "да" : "нет"); модель: \(model)\n"
    }
}

extension ChatRetrieval.Params {
    /// Прежние параметры проб: 5×1200, бюджет поиска по умолчанию — только
    /// для сравнения рук в бенче, в продукте их нет.
    static let legacyProbe = ChatRetrieval.Params(limit: 5, snippet: 1200,
                                                  budget: ArchiveSearch.defaultBudget)
}
