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
        guard let benchPath = ProcessInfo.processInfo.environment["CHAROITE_BENCH"],
              let graphRaw = ProcessInfo.processInfo.environment["CHAROITE_GRAPH_DIR"],
              !benchPath.isEmpty, !graphRaw.isEmpty else { throw XCTSkip("бенч не задан") }
        let graph = URL(fileURLWithPath: (graphRaw as NSString).expandingTildeInPath)
        let cases = try parse(URL(fileURLWithPath: (benchPath as NSString).expandingTildeInPath))
        XCTAssertFalse(cases.isEmpty, "вопросы не разобрались")

        // Проверяем ОТВЕТ, а не только выдачу поиска: мерить надо то, что
        // доходит до человека.
        let withModel = ProcessInfo.processInfo.environment["CHAROITE_BENCH_ANSWERS"] == "1"
        let (armName, params) = ProbeTurn.arm
        var report = ProbeTurn.header(withModel
            ? "БЕНЧ ПАМЯТИ (сквозной: поиск → синтез тем же ходом, что чат)"
            : "БЕНЧ ПАМЯТИ (только выдача поиска; CHAROITE_BENCH_ANSWERS=1 — со синтезом)")
        var hitTotal = 0, mustTotal = 0
        var unanswered: [String] = []
        var dump = ""   // выдача поиска по вопросам — видно, что дошло до модели
        for c in cases {
            let found = await ProbeTurn.search(c.q, params: params, root: graph).text
            dump += "=== \(c.q)\n\(found)\n\n"
            let subject: String
            if withModel {
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
        let reportPath = "/tmp/charoite_bench_\(armName).txt"
        ProbeTurn.writePrivate(report, to: reportPath)
        ProbeTurn.writePrivate(dump, to: "/tmp/charoite_bench_\(armName)_found.txt")
        print(report)
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
