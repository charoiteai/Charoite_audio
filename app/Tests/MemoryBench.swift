import XCTest
@testable import CharoiteApp

/// Бенч качества памяти по БОЕВОМУ контуру.
///
/// Прежний scripts/memory_bench.py ходил в brain-сервер (:8100) или в
/// собственный python-фолбэк — то есть мерил другую реализацию, а не тот
/// поиск, которым пользуется приложение. Ранжирование при этом менялось:
/// блоки вместо файлов, вес по роли документа, бюджет контекста. Проверять
/// такие изменения на глаз нельзя.
///
/// Набор вопросов приватен (он про рабочие встречи) и лежит вне репозитория:
///   CHAROITE_BENCH=~/путь/memory_bench.yaml CHAROITE_GRAPH_DIR=~/путь/граф \
///     swift test --package-path app --filter MemoryBench
final class MemoryBench: XCTestCase {
    private struct Case { let q: String; let must: [String] }

    func testRecallOfExpectedFacts() async throws {
        guard let benchPath = ProcessInfo.processInfo.environment["CHAROITE_BENCH"],
              let graphRaw = ProcessInfo.processInfo.environment["CHAROITE_GRAPH_DIR"],
              !benchPath.isEmpty, !graphRaw.isEmpty else { throw XCTSkip("бенч не задан") }
        let graph = URL(fileURLWithPath: (graphRaw as NSString).expandingTildeInPath)
        let cases = try parse(URL(fileURLWithPath: (benchPath as NSString).expandingTildeInPath))
        XCTAssertFalse(cases.isEmpty, "вопросы не разобрались")

        // Проверяем ОТВЕТ, а не только выдачу поиска: часть ожидаемых фактов —
        // это формулировки самого ответа («неизвестно»), их в документах нет
        // и быть не может. Мерить надо то, что доходит до человека.
        let withModel = ProcessInfo.processInfo.environment["CHAROITE_BENCH_ANSWERS"] == "1"
        var report = withModel
            ? "БЕНЧ ПАМЯТИ (сквозной: поиск → синтез)\n"
            : "БЕНЧ ПАМЯТИ (только выдача поиска; CHAROITE_BENCH_ANSWERS=1 — со синтезом)\n"
        var hitTotal = 0, mustTotal = 0
        for c in cases {
            let found = await ArchiveSearch.localSearch(query: c.q, limit: 5,
                                                        snippet: 1200, root: graph)
            let subject = withModel ? (await answer(question: c.q, context: found) ?? found) : found
            let low = norm(subject)
            let hits = c.must.filter { low.contains(norm($0)) }
            hitTotal += hits.count
            mustTotal += c.must.count
            let missed = c.must.filter { !low.contains(norm($0)) }
            report += "\n· \(c.q)\n  факты \(hits.count)/\(c.must.count)"
            if !missed.isEmpty { report += ", нет: \(missed.joined(separator: ", "))" }
        }
        let ratio = Double(hitTotal) / Double(max(mustTotal, 1))
        report += "\n\nИТОГО: \(hitTotal)/\(mustTotal) фактов в выдаче поиска "
                + "(\(Int(ratio * 100))%)\n"
        try? report.write(to: URL(fileURLWithPath: "/tmp/charoite_bench.txt"),
                          atomically: true, encoding: .utf8)
        print(report)
        // Порог ниже текущего уровня, но не вдвое: при 0.5 падение с 13/14 до
        // 8/14 прошло бы незамеченным, а это уже другой продукт. Запас нужен,
        // потому что граф живой — вопросы про его содержимое естественно
        // дрейфуют, и краснеть от этого бенч не должен.
        XCTAssertGreaterThan(ratio, 0.75,
                             "качество памяти просело: \(hitTotal)/\(mustTotal) — "
                             + "смотри /tmp/charoite_bench.txt, там видно, какие факты потерялись")
    }

    /// Синтез ответа локальной моделью — тот же путь, что в приложении.
    private func answer(question: String, context: String) async -> String? {
        guard let url = URL(string: AppSettings.ollamaURL + "/api/chat") else { return nil }
        let prompt = """
        Фрагменты архива встреч:

        \(context)

        Вопрос: \(question)

        Отвечай кратко и только по фрагментам. Если ответа в них нет — скажи «неизвестно».
        """
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 600
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "model": "qwen3.6:35b-a3b", "stream": false, "think": false,
            // temperature 0: бенч должен сравнивать ПРАВКИ, а не ловить
            // разброс генерации. С дефолтной температурой один и тот же код
            // давал 11, 13 и снова 11 из 14 — по таким числам нельзя судить,
            // помогла правка или повезло.
            "options": ["num_ctx": 32768, "num_predict": 400, "temperature": 0],
            "messages": [["role": "user", "content": prompt]],
        ] as [String: Any])
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]     // системный прокси ломает localhost
        guard let (data, _) = try? await URLSession(configuration: cfg).data(for: req),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let msg = obj["message"] as? [String: Any] else { return nil }
        return msg["content"] as? String
    }

    private func norm(_ s: String) -> String {
        s.lowercased().replacingOccurrences(of: "ё", with: "е")
    }

    /// Минимальный разбор: файл — список записей «- q:» и «must: [...]».
    /// Полноценный YAML-парсер сюда тянуть незачем.
    private func parse(_ url: URL) throws -> [Case] {
        let text = try String(contentsOf: url, encoding: .utf8)
        var out: [Case] = []
        var q: String?
        for raw in text.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("- q:") {
                q = unquote(String(line.dropFirst(4)))
            } else if line.hasPrefix("must:"), let question = q {
                let inside = line.drop(while: { $0 != "[" }).dropFirst().prefix(while: { $0 != "]" })
                let must = inside.split(separator: ",").map { unquote(String($0)) }
                    .filter { !$0.isEmpty }
                if !must.isEmpty { out.append(Case(q: question, must: must)) }
                q = nil
            }
        }
        return out
    }

    private func unquote(_ s: String) -> String {
        s.trimmingCharacters(in: .whitespaces)
            .trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
    }
}
