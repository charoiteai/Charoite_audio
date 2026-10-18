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
        for q in questions {
            let outcome = await ProbeTurn.search(q, params: params, root: graph)
            report += "\n──────── ВОПРОС: \(q)\nИСТОЧНИКИ (\(outcome.text.count) знаков):\n"
            for s in outcome.rels ?? [] { report += "   · \(s)\n" }
            let answer = await ProbeTurn.answer(question: q, vault: outcome.text) ?? "(модель не ответила)"
            report += "ОТВЕТ МОДЕЛИ:\n\(answer)\n"
        }
        // print из XCTest теряется при перенаправлении вывода — пишем в файл.
        let out = "/tmp/charoite_answer_probe_\(armName).txt"
        XCTAssertTrue(ProbeTurn.writePrivate(report, to: out), "отчёт не записан: \(out)")
        print(report)
    }
}
