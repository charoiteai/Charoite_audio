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
        let env = ProcessInfo.processInfo.environment
        guard env["CHAROITE_ANSWER_PROBE"] == "1" else { throw XCTSkip("не запрошено") }
        let graph = try ProbeTurn.graphDir(env["CHAROITE_GRAPH_DIR"] ?? "")
        let (armName, params) = ProbeTurn.arm
        let out = try ProbeTurn.reportPath("answer_probe_\(armName)")
        var report = ProbeTurn.header("ПРОБА ОТВЕТОВ")
        var unanswered = 0, emptySearch = 0
        for q in questions {
            let outcome = await ProbeTurn.search(q, params: params, root: graph)
            if outcome.text.isEmpty { emptySearch += 1 }
            report += "\n──────── ВОПРОС: \(q)\nИСТОЧНИКИ (\(outcome.text.count) знаков):\n"
            for s in outcome.rels ?? [] { report += "   · \(s)\n" }
            let answer = await ProbeTurn.answer(question: q, vault: outcome.text)
            if answer == nil { unanswered += 1 }
            report += "ОТВЕТ МОДЕЛИ:\n\(answer ?? "(модель не ответила)")\n"
        }
        // print из XCTest теряется при перенаправлении вывода — пишем в файл.
        print(report)
        guard ProbeTurn.writePrivate(report, to: out) else {
            XCTFail("отчёт не записан, прежний мог остаться: \(out)")
            return
        }
        print("отчёт: \(out)")
        // Молчание модели — не ответ: зелёная проба при остановленной Ollama
        // ничего не проверила (круг 3).
        // Пустой поиск по одному вопросу — законный результат, по всем — не тот граф.
        XCTAssertLessThan(emptySearch, questions.count, "поиск пуст по всем вопросам — проверь граф: \(graph.path)")
        XCTAssertEqual(unanswered, 0, "модель не ответила на \(unanswered) из \(questions.count) — "
                       + "проверь Ollama (\(ProbeTurn.model)); смотри \(out)")
    }
}
