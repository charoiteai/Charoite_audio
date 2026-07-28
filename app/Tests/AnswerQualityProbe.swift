import XCTest
@testable import CharoiteApp

/// Живая проверка качества ответов: реальный граф, реальная Ollama.
///
/// Не гейт — наблюдение. Печатает, что нашёл поиск и что ответила модель,
/// чтобы качество можно было оценить глазами, а не по косвенным метрикам.
///
///   CHAROITE_ANSWER_PROBE=1 CHAROITE_GRAPH_DIR=~/путь \
///     swift test --package-path app --filter AnswerQualityProbe
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

        for q in questions {
            let found = await ArchiveSearch.localSearch(query: q, limit: 5,
                                                        snippet: 1200, root: graph)
            let sources = found.components(separatedBy: "\n\n")
                .compactMap { $0.split(whereSeparator: \.isNewline).first }
                .map { String($0.dropFirst(2)) }
            print("\n──────── ВОПРОС: \(q)")
            print("ИСТОЧНИКИ (\(found.count) знаков):")
            for s in sources { print("   · \(s)") }
            if let answer = await ask(question: q, context: found) {
                print("ОТВЕТ МОДЕЛИ:\n\(answer)")
            } else {
                print("ОТВЕТ: модель недоступна")
            }
        }
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
        guard let (data, _) = try? await URLSession.shared.data(for: req),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let msg = obj["message"] as? [String: Any] else { return nil }
        return msg["content"] as? String
    }
}
