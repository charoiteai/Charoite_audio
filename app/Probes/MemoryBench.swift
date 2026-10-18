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
///     swift test --package-path app --filter CharoiteAppLiveProbes.MemoryBench
final class MemoryBench: XCTestCase {
    private struct Case { let q: String; let must: [String] }

    func testRecallOfExpectedFacts() async throws {
        let env = ProcessInfo.processInfo.environment
        guard let benchPath = env["CHAROITE_BENCH"], !benchPath.isEmpty else { throw XCTSkip("бенч не задан") }
        // Бенч запрошен — дальше любой недостающий вход валит прогон, а не пропускает его.
        let graph = try ProbeTurn.graphDir(env["CHAROITE_GRAPH_DIR"])
        let cases = try parse(URL(fileURLWithPath: (benchPath as NSString).expandingTildeInPath))
        // пустой набор — диагноз парсера, а не графа: страж пустой выдачи ниже соврал бы
        guard !cases.isEmpty else { XCTFail("вопросы не разобрались: \(benchPath)"); return }

        // Проверяем ОТВЕТ, а не только выдачу поиска: мерить надо то, что
        // доходит до человека.
        let withModel = try ProbeTurn.flag("CHAROITE_BENCH_ANSWERS")
        let (armName, params) = ProbeTurn.arm
        let reportPath = try ProbeTurn.reportPath("bench_\(armName)")
        let foundPath = try ProbeTurn.reportPath("bench_\(armName)_found")
        try await ProbeTurn.isolateIndex(self)
        var report = ProbeTurn.header(withModel
            ? "БЕНЧ ПАМЯТИ (сквозной: поиск → синтез тем же ходом, что чат)"
            : "БЕНЧ ПАМЯТИ (только выдача поиска; CHAROITE_BENCH_ANSWERS=1 — со синтезом)")
        var hitTotal = 0, mustTotal = 0
        var unanswered: [String] = []
        var dump = ""   // выдача поиска по вопросам — видно, что дошло до модели
        var emptyFound = 0
        for c in cases {
            let found = try await ProbeTurn.search(c.q, params: params, root: graph).text
            if found.isEmpty { emptyFound += 1 }
            dump += "=== \(c.q)\n\(found)\n\n"
            let subject: String
            if withModel {
                // Без источников модель ответила бы из весов — память это не мерит:
                // факты вопроса в знаменателе с нулём, как в пробе ответов (круг 7).
                guard !found.isEmpty else {
                    mustTotal += c.must.count
                    report += "\n· \(c.q)\n  факты 0/\(c.must.count), источников нет — модель не спрашиваю"
                    continue
                }
                // Модель не ответила — факты вопроса в знаменателе с нулём: подмена
                // ответа выдачей поиска засчитывала факты, которых человек не увидит,
                // а выброс вопроса поднимал процент тем выше, чем больше молчала модель.
                guard let answer = await ProbeTurn.answer(question: c.q, vault: found) else {
                    unanswered.append(c.q)
                    mustTotal += c.must.count
                    continue
                }
                subject = answer
            } else {
                subject = found
            }
            let low = norm(subject)
            let hits = c.must.filter { low.contains(norm($0)) }
            hitTotal += hits.count
            mustTotal += c.must.count
            let missed = c.must.filter { !low.contains(norm($0)) }
            report += "\n· \(c.q)\n  факты \(hits.count)/\(c.must.count)"
            if !missed.isEmpty { report += ", нет: \(missed.joined(separator: ", "))" }
            // ответ целиком — разбор потерянного факта глазами без второго прогона
            if withModel { report += "\n  ответ: " + subject.replacingOccurrences(of: "\n", with: "\n  ") }
        }
        if !unanswered.isEmpty {
            report += "\n\nМОДЕЛЬ НЕ ОТВЕТИЛА (факты в счёте с нулём): \(unanswered.count)\n"
                + unanswered.map { "· \($0)" }.joined(separator: "\n")
        }
        let ratio = Double(hitTotal) / Double(max(mustTotal, 1))
        report += "\n\nИТОГО: \(hitTotal)/\(mustTotal) фактов \(withModel ? "в ответе" : "в выдаче поиска") "
                + "(\(Int(ratio * 100))%)\n"
        // файл — по руке: второй прогон другой руки не затирает первый
        print(report)
        guard ProbeTurn.writePrivate(report, to: reportPath) else {
            XCTFail("отчёт не записан, прежний мог остаться: \(reportPath)")
            return
        }
        guard ProbeTurn.writePrivate(dump, to: foundPath) else {
            XCTFail("выдача не записана, прежняя могла остаться: \(foundPath) (отчёт записан: \(reportPath))")
            return
        }
        print("отчёт: \(reportPath)\nвыдача: \(foundPath)")
        // Пустая выдача по всем вопросам — не тот граф: модель ответила бы из весов (круг 6).
        guard emptyFound < cases.count else {
            XCTFail("поиск пуст по всем вопросам — проверь граф: \(graph.path)")
            return
        }
        // Молчание модели — не просадка памяти: свой диагноз до порога.
        guard unanswered.isEmpty else {
            XCTFail("модель не ответила на \(unanswered.count) из \(cases.count) — проверь Ollama "
                    + "(\(ProbeTurn.model)); смотри \(reportPath)")
            return
        }
        // Порог ниже текущего уровня, но не вдвое: при 0.5 падение с 13/14 до
        // 8/14 прошло бы незамеченным, а это уже другой продукт. Запас нужен,
        // потому что граф живой — вопросы про его содержимое естественно
        // дрейфуют, и краснеть от этого бенч не должен.
        XCTAssertGreaterThan(ratio, 0.75,
                             "качество памяти просело: \(hitTotal)/\(mustTotal) — "
                             + "смотри \(reportPath), там видно, какие факты потерялись")
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
