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
    /// `abstain` — в графе ответа нет (`must: []`), верный ответ — отсутствие:
    /// такие вопросы прежний разбор выбрасывал, и воздержание бенч не мерил (№634).
    private struct Case { let q: String; let must: [String]; let abstain: Bool }

    /// Пороги нового счёта — в наборе, не в коде пробы: строки верхнего
    /// уровня `max_false_refusal: N` и `min_abstain: 0.5` (out1, Sonnet I4,
    /// DS I). Нет строки — счёт только в отчёте.
    private struct Gate { var maxFalseRefusal: Int?; var minAbstain: Double? }

    func testRecallOfExpectedFacts() async throws {
        let env = ProcessInfo.processInfo.environment
        guard let benchPath = env["CHAROITE_BENCH"], !benchPath.isEmpty else { throw XCTSkip("бенч не задан") }
        // Бенч запрошен — дальше любой недостающий вход валит прогон, а не пропускает его.
        let graph = try ProbeTurn.graphDir(env["CHAROITE_GRAPH_DIR"])
        let (cases, gate) = try parse(URL(fileURLWithPath: (benchPath as NSString).expandingTildeInPath))
        // пустой набор — диагноз парсера, а не графа: страж пустой выдачи ниже соврал бы
        guard !cases.isEmpty else { XCTFail("вопросы не разобрались: \(benchPath)"); return }

        // Проверяем ОТВЕТ, а не только выдачу поиска: мерить надо то, что
        // доходит до человека.
        let withModel = try ProbeTurn.flag("CHAROITE_BENCH_ANSWERS")
        let (armName, params) = ProbeTurn.arm
        // ответы и выдача — разные файлы: прогон со синтезом не затирает отчёт выдачи
        let kind = withModel ? "_answers" : ""
        let reportPath = try ProbeTurn.reportPath("bench_\(armName)\(kind)")
        let foundPath = try ProbeTurn.reportPath("bench_\(armName)\(kind)_found")
        try await ProbeTurn.isolateIndex(self)
        var report = ProbeTurn.header(withModel
            ? "БЕНЧ ПАМЯТИ (сквозной: поиск → синтез тем же ходом, что чат)"
            : "БЕНЧ ПАМЯТИ (только выдача поиска; CHAROITE_BENCH_ANSWERS=1 — со синтезом)")
        var hitTotal = 0, mustTotal = 0
        var unanswered: [String] = []
        var dump = ""   // выдача поиска по вопросам — видно, что дошло до модели
        var emptyFound = 0
        // Имена — как у python-сверки (scripts/retrieval_compare.py):
        // false_refusal — отказ при факте в выдаче, false_answer — ответ на
        // вопрос без ответа в графе. Счёт — по вердикту поиска.
        var falseRefusal: [ArchiveSearch.Verdict: Int] = [:]
        var abstainOK = 0, abstainTotal = 0, abstainUnproven = 0
        for c in cases {
            let outcome = try await ProbeTurn.search(c.q, params: params, root: graph)
            let found = outcome.text
            if found.isEmpty && !c.abstain { emptyFound += 1 }
            dump += "=== \(c.q) [\(outcome.verdict.rawValue)]\n\(found)\n\n"
            let verdictLine = "вердикт поиска: \(outcome.verdict.rawValue)"
            if c.abstain {
                abstainTotal += 1
                // Чат зовёт модель и на пустой выдаче — бенч мерит тот же ход.
                // Без модели воздержанием поиска считается любой вердикт,
                // кроме «нашлось».
                if withModel {
                    guard let answer = await ProbeTurn.answer(question: c.q, outcome: outcome) else {
                        unanswered.append(c.q)
                        continue
                    }
                    let ok = ChatRetrieval.isAbsence(answer, lines: 2)
                    if ok { abstainOK += 1 }
                    report += "\n· \(c.q)\n  без ответа в графе: \(ok ? "воздержался" : "false_answer"), \(verdictLine)"
                    report += "\n  ответ: " + answer.replacingOccurrences(of: "\n", with: "\n  ")
                } else {
                    // воздержание без модели — только проверенное «пусто/слабо»:
                    // лёгший эмбеддер (unverified) успехом не считается (out1, Sonnet M2)
                    let ok = ChatRetrieval.honesty(outcome.verdict, hasText: true).allowsAbsence
                    if ok { abstainOK += 1 }
                    if outcome.verdict == .unverified || outcome.verdict == .unavailable { abstainUnproven += 1 }
                    report += "\n· \(c.q)\n  без ответа в графе: \(ok ? "поиск не нашёл" : "поиск не доказал"), \(verdictLine)"
                }
                continue
            }
            let subject: String
            if withModel {
                // Без источников модель ответила бы из весов — память это не мерит:
                // факты вопроса в знаменателе с нулём, как в пробе ответов (круг 7).
                guard !found.isEmpty else {
                    mustTotal += c.must.count
                    report += "\n· \(c.q)\n  факты 0/\(c.must.count), источников нет — модель не спрашиваю, \(verdictLine)"
                    continue
                }
                // Модель не ответила — факты вопроса в знаменателе с нулём: подмена
                // ответа выдачей поиска засчитывала факты, которых человек не увидит,
                // а выброс вопроса поднимал процент тем выше, чем больше молчала модель.
                guard let answer = await ProbeTurn.answer(question: c.q, outcome: outcome) else {
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
            report += ", \(verdictLine)"
            if withModel {
                let inFound = c.must.filter { norm(found).contains(norm($0)) }.count
                if ChatRetrieval.isAbsence(subject) && inFound > 0 {
                    falseRefusal[outcome.verdict, default: 0] += 1
                    report += "\n  false_refusal: отказ при \(inFound)/\(c.must.count) фактах в выдаче"
                }
                // ответ целиком — разбор потерянного факта глазами без второго прогона
                report += "\n  ответ: " + subject.replacingOccurrences(of: "\n", with: "\n  ")
            }
        }
        if !unanswered.isEmpty {
            report += "\n\nМОДЕЛЬ НЕ ОТВЕТИЛА (факты в счёте с нулём): \(unanswered.count)\n"
                + unanswered.map { "· \($0)" }.joined(separator: "\n")
        }
        let ratio = Double(hitTotal) / Double(max(mustTotal, 1))
        if withModel {
            let byVerdict = ArchiveSearch.Verdict.allCases.compactMap { v in
                falseRefusal[v].map { "\(v.rawValue) \($0)" } }
            report += "\n\nFALSE_REFUSAL: \(falseRefusal.values.reduce(0, +)) (отказ при факте в выдаче"
                + (byVerdict.isEmpty ? ")" : "; \(byVerdict.joined(separator: ", ")))")
        }
        if abstainTotal > 0 {
            report += "\nВОЗДЕРЖАНИЕ: \(abstainOK)/\(abstainTotal) вопросов без ответа в графе "
                + (withModel ? "(ответ — отказ)" : "(поиск не нашёл; не доказал — \(abstainUnproven))")
        }
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
        // набор из одних вопросов без ответа этот страж не судит (out1, DS M3)
        let answerable = cases.filter { !$0.abstain }.count
        guard answerable == 0 || emptyFound < answerable else {
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
        if answerable > 0 {
            XCTAssertGreaterThan(ratio, 0.75,
                                 "качество памяти просело: \(hitTotal)/\(mustTotal) — "
                                 + "смотри \(reportPath), там видно, какие факты потерялись")
        }
        let refusals = falseRefusal.values.reduce(0, +)
        if withModel, let cap = gate.maxFalseRefusal {
            XCTAssertLessThanOrEqual(refusals, cap, "отказ при факте в выдаче: \(refusals) — смотри \(reportPath)")
        }
        if let floor = gate.minAbstain, abstainTotal > 0 {
            XCTAssertGreaterThanOrEqual(Double(abstainOK) / Double(abstainTotal), floor,
                                        "воздержание \(abstainOK)/\(abstainTotal) ниже порога — смотри \(reportPath)")
        }
    }

    private func norm(_ s: String) -> String {
        s.lowercased().replacingOccurrences(of: "ё", with: "е")
    }

    /// Минимальный разбор: файл — список записей «- q:», «must: [...]» и
    /// необязательных «abstain: true» и «cat: other_graph». Запись копится до следующего «- q:»:
    /// поле после «must:» прежде терялось. Пустой `must` без `abstain` и
    /// `abstain` с фактами — ошибка набора, а не тихий пропуск.
    /// Полноценный YAML-парсер сюда тянуть незачем.
    private func parse(_ url: URL) throws -> ([Case], Gate) {
        let text = try String(contentsOf: url, encoding: .utf8)
        var out: [Case] = []
        var gate = Gate()
        var q: String?
        var must: [String]?
        var abstain = false
        var otherGraph = false
        func flush() throws {
            defer { q = nil; must = nil; abstain = false; otherGraph = false }
            guard let question = q else { return }
            // Вопрос по другому графу владельца: в этом графе ответа нет, верно —
            // отсутствие; его факты сверены по тому графу и здесь не считаются.
            if otherGraph {
                out.append(Case(q: question, must: [], abstain: true))
                return
            }
            let facts = must ?? []
            if abstain && !facts.isEmpty {
                throw ProbeTurn.SetupError(description: "abstain с фактами: \(question)")
            }
            if !abstain && facts.isEmpty {
                throw ProbeTurn.SetupError(description: "нет must и нет abstain: \(question)")
            }
            out.append(Case(q: question, must: facts, abstain: abstain))
        }
        for raw in text.split(separator: "\n", omittingEmptySubsequences: false) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if raw.hasPrefix("max_false_refusal:") || raw.hasPrefix("min_abstain:") {
                let (key, value) = (line.prefix(while: { $0 != ":" }),
                                    line.drop(while: { $0 != ":" }).dropFirst().trimmingCharacters(in: .whitespaces))
                // кривой порог молча снял бы гейт
                if key == "max_false_refusal", let n = Int(value) { gate.maxFalseRefusal = n }
                else if key == "min_abstain", let x = Double(value) { gate.minAbstain = x }
                else { throw ProbeTurn.SetupError(description: "порог набора не разобрался: \(line)") }
            } else if line.hasPrefix("- q:") {
                try flush()
                q = unquote(String(line.dropFirst(4)))
            } else if line.hasPrefix("must:"), q != nil {
                let inside = line.drop(while: { $0 != "[" }).dropFirst().prefix(while: { $0 != "]" })
                must = inside.split(separator: ",").map { unquote(String($0)) }
                    .filter { !$0.isEmpty }
            } else if line.hasPrefix("cat:"), q != nil {
                otherGraph = unquote(String(line.dropFirst("cat:".count))) == "other_graph"
            } else if line.hasPrefix("abstain:"), q != nil {
                abstain = line.dropFirst("abstain:".count).trimmingCharacters(in: .whitespaces) == "true"
            }
        }
        try flush()
        return (out, gate)
    }

    private func unquote(_ s: String) -> String {
        s.trimmingCharacters(in: .whitespaces)
            .trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
    }
}
