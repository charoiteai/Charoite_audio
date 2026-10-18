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

        let (armName, params) = ProbeTurn.arm
        var report = ProbeTurn.header("ПРОБА ОТВЕТОВ")
        var unanswered = 0
        for q in questions {
            let outcome = await ProbeTurn.search(q, params: params, root: graph)
            report += "\n──────── ВОПРОС: \(q)\nИСТОЧНИКИ (\(outcome.text.count) знаков):\n"
            for s in outcome.rels ?? [] { report += "   · \(s)\n" }
            let answer = await ProbeTurn.answer(question: q, vault: outcome.text)
            if answer == nil { unanswered += 1 }
            report += "ОТВЕТ МОДЕЛИ:\n\(answer ?? "(модель не ответила)")\n"
        }
        // print из XCTest теряется при перенаправлении вывода — пишем в файл.
        let out = ProbeTurn.reportPath("answer_probe_\(armName)")
        print(report)
        guard ProbeTurn.writePrivate(report, to: out) else {
            XCTFail("новый отчёт не записан, прежний мог остаться: \(out)")
            return
        }
        // Молчание модели — не ответ: зелёная проба при остановленной Ollama
        // ничего не проверила (круг 3).
        XCTAssertEqual(unanswered, 0, "модель не ответила на \(unanswered) из \(questions.count) — "
                       + "проверь Ollama (\(ProbeTurn.model)); смотри \(out)")
    }
}
