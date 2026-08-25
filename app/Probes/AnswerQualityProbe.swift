import XCTest
@testable import CharoiteApp

/// Живая проверка качества ответов: реальный граф, реальная Ollama.
///
/// Не гейт — наблюдение. Печатает, что нашёл поиск и что ответила модель,
/// чтобы качество можно было оценить глазами, а не по косвенным метрикам.
///
///   CHAROITE_ANSWER_PROBE=1 CHAROITE_GRAPH_DIR=~/путь \
///     swift test --package-path app \
///       --filter CharoiteAppLiveProbes.AnswerQualityProbe
final class AnswerQualityProbe: XCTestCase {
    private let questions = [
        "что решили по витрине сертификации",
        "какие блокеры по доступу к LLM",
        "кто отвечает за пилот и что с ним сейчас",
    ]

    func testAnswers() async throws {
        guard ProcessInfo.processInfo.environment["CHAROITE_ANSWER_PROBE"] == "1",
              let raw = ProcessInfo.processInfo.environment["CHAROITE_GRAPH_DIR"],
              !raw.isEmpty else { throw XCTSkip("не запрошено") }
        let graph = URL(fileURLWithPath: (raw as NSString).expandingTildeInPath)

        var report = ""
        for q in questions {
            let found = await ArchiveSearch.localSearch(query: q, limit: 5,
                                                        snippet: 1200, root: graph)
            let sources = found.components(separatedBy: "\n\n")
                .compactMap { $0.split(whereSeparator: \.isNewline).first }
                .map { String($0.dropFirst(2)) }
            report += "\n──────── ВОПРОС: \(q)\nИСТОЧНИКИ (\(found.count) знаков):\n"
            for s in sources { report += "   · \(s)\n" }
            let answer = await ask(question: q, context: found) ?? "(модель недоступна)"
            report += "ОТВЕТ МОДЕЛИ:\n\(answer)\n"
        }
        // print из XCTest теряется при перенаправлении вывода — пишем в файл.
        let out = URL(fileURLWithPath: "/tmp/charoite_answer_probe.txt")
        try? report.write(to: out, atomically: true, encoding: .utf8)
        print(report)
    }

    private func ask(question: String, context: String) async -> String? {
        guard let url = URL(string: AppSettings.ollamaURL + "/api/chat") else { return nil }
        let prompt = """
        Фрагменты архива встреч:

        \(context)

        Вопрос: \(question)

        Ответь коротко и только по фрагментам. Если ответа в них нет — так и скажи.
        Указывай источник (имя файла) для каждого факта.
        """
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 600
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "model": "qwen3.6:35b-a3b", "stream": false, "think": false,
            "options": ["num_ctx": 32768, "num_predict": 500],
            "messages": [["role": "user", "content": prompt]],
        ] as [String: Any])
        // Прокси обнуляем, как в проде: системный прокси (у пользователя он
        // может стоять ради других сервисов) ломает запрос к localhost —
        // именно на этом первая версия пробы молча возвращала пустой ответ.
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]
        guard let (data, _) = try? await URLSession(configuration: cfg).data(for: req),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let msg = obj["message"] as? [String: Any] else { return nil }
        return msg["content"] as? String
    }
}
