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
        guard try ProbeTurn.flag("CHAROITE_ANSWER_PROBE") else { throw XCTSkip("не запрошено") }
        let graph = try ProbeTurn.graphDir(env["CHAROITE_GRAPH_DIR"])
        let arm = try ProbeTurn.arm()
        let (armName, params) = arm
        let out = try ProbeTurn.reportPath("answer_probe_\(armName)")
        let index = try await ProbeTurn.isolateIndex(self)
        var report = ProbeTurn.header("ПРОБА ОТВЕТОВ", arm: arm)
        var unanswered = 0, emptySearch = 0
        for q in questions {
            let outcome = await ProbeTurn.search(q, params: params, root: graph, index: index)
            report += "\n──────── ВОПРОС: \(q)\nИСТОЧНИКИ (\(outcome.text.count) знаков):\n"
            for s in outcome.rels ?? [] { report += "   · \(s)\n" }
            // ответ без источников — из весов модели, качество памяти он не показывает
            guard !outcome.text.isEmpty else {
                emptySearch += 1
                report += "(источников нет — модель не спрашиваю)\n"
                continue
            }
            let answer = await ProbeTurn.answer(question: q, outcome: outcome)
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
        // Пустой поиск по одному вопросу — законный результат, по всем — не тот граф.
        XCTAssertLessThan(emptySearch, questions.count, "поиск пуст по всем вопросам — проверь граф: \(graph.path)")
        // Молчание модели — не ответ: зелёная проба при остановленной Ollama
        // ничего не проверила (круг 3).
        XCTAssertEqual(unanswered, 0, "модель не ответила на \(unanswered) из \(questions.count) — "
                       + "проверь Ollama (\(ProbeTurn.model)); смотри \(out)")
    }
}
