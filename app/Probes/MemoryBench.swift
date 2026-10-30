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
        let (cases, gate) = try parse(URL(fileURLWithPath: (benchPath as NSString).expandingTildeInPath))
        // пустой набор — диагноз парсера, а не графа: страж пустой выдачи ниже соврал бы
        guard !cases.isEmpty else { XCTFail("вопросы не разобрались: \(benchPath)"); return }
        // Слой 2: синтез по сохранённым пачкам, без графа и поиска (№752)
        if let replay = env["CHAROITE_BENCH_REPLAY"], !replay.isEmpty {
            try await replayPackets(replay, cases: cases)
            return
        }
        let graph = try ProbeTurn.graphDir(env["CHAROITE_GRAPH_DIR"])

        // Проверяем ОТВЕТ, а не только выдачу поиска: мерить надо то, что
        // доходит до человека.
        let withModel = try ProbeTurn.flag("CHAROITE_BENCH_ANSWERS")
        let arm = try ProbeTurn.arm()
        let (armName, params) = arm
        // ответы и выдача — разные файлы: прогон со синтезом не затирает отчёт выдачи
        let kind = withModel ? "_answers" : ""
        let reportPath = try ProbeTurn.reportPath("bench_\(armName)\(kind)")
        let foundPath = try ProbeTurn.reportPath("bench_\(armName)\(kind)_found")
        let index = try await ProbeTurn.isolateIndex(self)
        let packets = try ProbeTurn.PacketLog("bench_packet_\(armName)\(kind)")
        // путь — сразу: оборванный прогон отчёта не пишет, а приватный файл остаётся (n752-out2, DS M3)
        print("пачки (0600, убрать после разбора): \(packets.path)")
        var report = ProbeTurn.header(withModel
            ? "БЕНЧ ПАМЯТИ (сквозной: поиск → синтез тем же ходом, что чат)"
            : "БЕНЧ ПАМЯТИ (только выдача поиска; CHAROITE_BENCH_ANSWERS=1 — со синтезом)", arm: arm)
        // Время поиска и синтеза — порознь: решение о слое 2 — по ним (n752-in1, Sonnet C2)
        let clock = ContinuousClock()
        var searchTime = Duration.zero, synthTime = Duration.zero
        var hitTotal = 0, mustTotal = 0
        var unanswered: [String] = []
        var dump = ""   // выдача поиска по вопросам — видно, что дошло до модели
        var emptyFound = 0
        // Имена — как у python-сверки (scripts/retrieval_compare.py):
        // false_refusal — отказ при факте в выдаче, false_answer — ответ на
        // вопрос без ответа в графе. Счёт — по вердикту поиска.
        var falseRefusal: [ArchiveSearch.Verdict: Int] = [:]
        var abstainOK = 0, abstainTotal = 0
        for c in cases {
            let started = clock.now
            let outcome = await ProbeTurn.search(c.q, params: params, root: graph, index: index)
            searchTime += started.duration(to: clock.now)
            let found = outcome.text
            let body = withModel ? ProbeTurn.body(question: c.q, outcome: outcome) : nil
            var reply: ProbeTurn.Reply?
            if let body, c.abstain || !found.isEmpty {
                let asked = clock.now
                reply = await ProbeTurn.send(body)
                synthTime += asked.duration(to: clock.now)
            }
            try packets.append(.init(arm: arm, q: c.q, must: c.must, abstain: c.abstain,
                                     outcome: outcome, body: body, answer: reply?.text))
            if found.isEmpty && !c.abstain { emptyFound += 1 }
            dump += "=== \(c.q) [\(outcome.verdict.rawValue)]\n\(found)\n\n"
            let verdictLine = "вердикт поиска: \(outcome.verdict.rawValue)"
            if c.abstain {
                abstainTotal += 1
                // Чат зовёт модель и на пустой выдаче — бенч мерит тот же ход.
                // Без модели воздержанием поиска считается любой вердикт,
                // кроме «нашлось».
                if withModel {
                    guard let answer = reply?.text else {
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
                guard let answer = reply?.text else {
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
            // Слой 1: где в выдаче руки стоит каждый факт — ранг куска, «гейт» или «нет»
            let ranks = c.must.map { fact -> String in
                switch ProbeTurn.factRank(fact, in: outcome.parts, norm: norm) {
                case .none: return "\(fact)→нет"
                case .some(-1): return "\(fact)→гейт"
                case .some(let r): return "\(fact)→\(r)"
                }
            }
            // длины кусков — рука pN не равна N кускам по 1000: упаковщик режет (n752-out1, Haiku C1)
            let lengths = (outcome.parts ?? []).map(\.text.count)
            let estimate: Int = ChatRetrieval.messageTokens(ProbeTurn.system(question: c.q, outcome: outcome))
                + ChatRetrieval.messageTokens(c.q)
            var rankLine = "\n  ранги: \(ranks.joined(separator: ", ")); кусков \(lengths.count)"
            if let lo = lengths.min(), let hi = lengths.max() { rankLine += " длиной \(lo)–\(hi) зн." }
            rankLine += ", оценка промпта \(estimate) ток."
            if let tokens = reply?.promptTokens { rankLine += ", промпт у Ollama \(tokens) ток." }
            report += rankLine
            if withModel {
                // судья прежний — вся выдача с шапками, как до №752: ранг судит только тела (n752-out3, Haiku I1)
                let inFound = c.must.filter { norm(outcome.text).contains(norm($0)) }.count
                // отказ первой строкой и ни одного факта в ответе: оговорка
                // «точной даты нет» перед найденным фактом — не отказ (out2, DS I2).
                // Судья — как в базе, по всем вердиктам (счёт по вердиктам ниже):
                // какой отказ разрешает правило — задача бенча в два слоя (out4)
                if ChatRetrieval.isAbsence(subject) && inFound > 0 && hits.isEmpty {
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
                + (withModel ? "(ответ — отказ)" : "(поиск доказал «нет»)")
        }
        // без модели отказа нет — пороги отказа и воздержания не судятся;
        // молча пропущенный порог читался бы как пройденный (out2)
        if !withModel && (gate.maxFalseRefusal != nil || gate.minAbstain != nil) {
            report += "\nПОРОГИ НАБОРА НЕ ПРИМЕНЕНЫ: прогон без модели (CHAROITE_BENCH_ANSWERS=1)"
        }
        report += "\n\nИТОГО: \(hitTotal)/\(mustTotal) фактов \(withModel ? "в ответе" : "в выдаче поиска") "
                + "(\(Int(ratio * 100))%)\n"
        report += "время: поиск \(seconds(searchTime)) с, синтез "
            + (withModel ? "\(seconds(synthTime)) с" : "— (модель не спрашивали)") + " на \(cases.count) вопр.\n"
        report += "пачки: \(packets.path)\n"
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
        if withModel, let floor = gate.minAbstain, abstainTotal > 0 {
            XCTAssertGreaterThanOrEqual(Double(abstainOK) / Double(abstainTotal), floor,
                                        "воздержание \(abstainOK)/\(abstainTotal) ниже порога — смотри \(reportPath)")
        }
    }

    private func seconds(_ d: Duration) -> String {
        String(format: "%.1f", Double(d.components.seconds) + Double(d.components.attoseconds) / 1e18)
    }

    /// Слой 2: синтез по пачкам прошлого прогона — без графа и поиска. Ход тот
    /// же (`ProbeTurn.body`/`send`), поэтому правка промпта видна; «запрос тот
    /// же» — тело совпало с записанным байт в байт. Ответ при температуре 0 —
    /// не оракул (n752-in1, DS I4): сравниваются факты обоих ответов, судья —
    /// записанный в пачке. Все пачки сверяются до первого запроса к модели
    /// (n752-out1, Sonnet I1); молчание модели валит прогон, как в прямом (DS C1).
    private func replayPackets(_ path: String, cases: [Case]) async throws {
        let (packets, torn) = try ProbeTurn.PacketLog.read(path)
        guard let first = packets.first else { throw ProbeTurn.SetupError(description: "пачек нет в файле: \(path)") }
        guard ProbeTurn.isKnownArm(first.arm), packets.allSatisfy({ $0.arm == first.arm }) else {
            throw ProbeTurn.SetupError(description: "в файле пачки разных или незнакомых рук: \(path)")
        }
        // две пачки одного вопроса — склейка двух прогонов, счёт по ним не тот (n752-out3, Haiku C1)
        guard Set(packets.map(\.q)).count == packets.count else {
            throw ProbeTurn.SetupError(description: "в файле две пачки одного вопроса: \(path)")
        }
        let byQ = Dictionary(cases.map { ($0.q, $0) }, uniquingKeysWith: { a, _ in a })
        var outcomes: [ArchiveSearch.Outcome] = []
        for p in packets {
            guard let c = byQ[p.q], c.must == p.must, c.abstain == p.abstain else {
                throw ProbeTurn.SetupError(description: "вопрос пачки не совпал с набором (нет или факты сменились): «\(p.q)»")
            }
            guard p.body != nil else {
                throw ProbeTurn.SetupError(description: "пачка без синтеза — повторять нечего, запиши с CHAROITE_BENCH_ANSWERS=1: «\(p.q)»")
            }
            // модель спрашивали и она промолчала — «при записи» вышло бы нулём (n752-out2, DS C1)
            guard p.answer != nil || !(p.abstain || !p.text.isEmpty) else {
                throw ProbeTurn.SetupError(description: "пачка записана при молчащей модели — эталоном не годится: «\(p.q)»")
            }
            // «?» — адрес не разобрался: сверка по нему ничего не доказывает (n752-out2, Haiku m-2)
            guard p.local == ProbeTurn.inferenceIsLocal, p.model == ProbeTurn.model, p.server == ProbeTurn.server,
                  !p.server.contains("?"), p.window == ChatRetrieval.Window.input else {
                throw ProbeTurn.SetupError(description: "пачка записана при \(p.model) на \(p.server), окно \(p.window) — "
                                           + "сейчас \(ProbeTurn.model) на \(ProbeTurn.server), окно "
                                           + "\(ChatRetrieval.Window.input): повтор не тот ход")
            }
            outcomes.append(try p.outcome())
        }
        let out = try ProbeTurn.reportPath("bench_replay_\(first.arm)")
        let missing = cases.filter { c in !packets.contains { $0.q == c.q } }.map(\.q)
        var report = "БЕНЧ ПАМЯТИ — ПОВТОР СИНТЕЗА ПО ПАЧКАМ\nрука: \(first.arm) (limit \(first.limit), "
            + "snippet \(first.snippet), budget \(first.budget)); модель: \(ProbeTurn.model) на \(ProbeTurn.server); "
            + "пачки: \(path)\nв наборе \(cases.count), пачек \(packets.count)"
            + (torn ? ", последняя строка оборвана — пропущена" : "")
            + (missing.isEmpty ? "" : "; без пачки: " + missing.joined(separator: " | ")) + "\n"
        let clock = ContinuousClock()
        var synthTime = Duration.zero
        var then = 0, now = 0, total = 0, sameBody = 0, sameAnswer = 0, asked = 0
        var abstainThen = 0, abstainNow = 0, abstainTotal = 0, refusalThen = 0, refusalNow = 0
        var unanswered: [String] = []
        for (p, outcome) in zip(packets, outcomes) {
            if !p.abstain && outcome.text.isEmpty {
                total += p.must.count
                report += "\n· \(p.q)\n  источников нет — модель не спрашиваю"
                continue
            }
            guard let body = ProbeTurn.body(question: p.q, outcome: outcome) else {
                throw ProbeTurn.SetupError(description: "тело запроса не собралось: «\(p.q)»")
            }
            asked += 1
            let same = p.body == String(decoding: body, as: UTF8.self)
            if same { sameBody += 1 }
            // запись считается до запроса: сегодняшнее молчание её не занижает (n752-out2, DS I1)
            let was = p.must.filter { norm(p.answer ?? "").contains(norm($0)) }.count
            let inFound = p.must.filter { norm(outcome.text).contains(norm($0)) }.count
            let absentThen = p.answer.map { ChatRetrieval.isAbsence($0, lines: 2) } ?? false
            if p.abstain {
                (abstainThen, abstainTotal) = (abstainThen + (absentThen ? 1 : 0), abstainTotal + 1)
            } else {
                (then, total) = (then + was, total + p.must.count)
                if inFound > 0 && was == 0 && ChatRetrieval.isAbsence(p.answer ?? "") { refusalThen += 1 }
            }
            let started = clock.now
            let reply = await ProbeTurn.send(body)
            synthTime += started.duration(to: clock.now)
            guard let answer = reply?.text else {
                unanswered.append(p.q)
                report += "\n· \(p.q)\n  запрос тот же: \(same ? "да" : "нет"), модель не ответила"
                continue
            }
            if answer == p.answer { sameAnswer += 1 }
            report += "\n· \(p.q)\n  запрос тот же: \(same ? "да" : "нет")"
            if p.abstain {
                let b = ChatRetrieval.isAbsence(answer, lines: 2)
                abstainNow += b ? 1 : 0
                report += ", воздержание при записи: \(absentThen ? "да" : "нет"), сейчас: \(b ? "да" : "нет")"
            } else {
                let got = p.must.filter { norm(answer).contains(norm($0)) }.count
                now += got
                report += ", факты при записи \(was)/\(p.must.count), сейчас \(got)/\(p.must.count)"
                // false_refusal — тем же судьёй, что прямой прогон (n752-out1, Haiku I6)
                if inFound > 0 && got == 0 && ChatRetrieval.isAbsence(answer) {
                    refusalNow += 1
                    report += "\n  false_refusal: отказ при \(inFound)/\(p.must.count) фактах в выдаче"
                }
            }
            report += "\n  ответ: " + answer.replacingOccurrences(of: "\n", with: "\n  ")
        }
        if !unanswered.isEmpty {
            report += "\n\nМОДЕЛЬ НЕ ОТВЕТИЛА (факты в счёте с нулём): \(unanswered.count)\n"
                + unanswered.map { "· \($0)" }.joined(separator: "\n")
        }
        report += "\n\nИТОГО: факты при записи \(then)/\(total), повтор \(now)/\(total); "
            + "запрос тот же \(sameBody)/\(asked), ответ дословно тот же \(sameAnswer)/\(asked)"
            + (abstainTotal > 0 ? "; воздержание при записи \(abstainThen)/\(abstainTotal), сейчас \(abstainNow)/\(abstainTotal)" : "")
            + "; false_refusal при записи \(refusalThen), сейчас \(refusalNow)"
            + "\nвремя: синтез \(seconds(synthTime)) с на \(asked) вопр.\n"
        print(report)
        XCTAssertTrue(ProbeTurn.writePrivate(report, to: out), "отчёт повтора не записан: \(out)")
        print("отчёт: \(out)")
        XCTAssertTrue(unanswered.isEmpty, "модель не ответила на \(unanswered.count) из \(asked) — проверь Ollama "
                      + "(\(ProbeTurn.model)); смотри \(out)")
        // неполный файл и файл без измеренных вопросов — не эталон (n752-out2, Sonnet I2, DS I2)
        XCTAssertTrue(missing.isEmpty && packets.count == cases.count && !torn, "пачки не на весь набор (\(packets.count) из \(cases.count)) — смотри \(out)")
        XCTAssertGreaterThan(asked, 0, "повтор не спросил модель ни разу — пачки без выдачи, смотри \(out)")
    }

    /// Судья фактов: регистр, «ё», и любые цепочки пробелов — один пробел:
    /// факт, разорванный переводом строки во фрагменте, не теряется (n752-out1, Sonnet I6).
    private func norm(_ s: String) -> String {
        s.lowercased().replacingOccurrences(of: "ё", with: "е")
            .split(whereSeparator: \.isWhitespace).joined(separator: " ")
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
            // повтор сверяет пачки с набором по тексту вопроса: дубль склеил бы два в один (n752-out3, Haiku C1)
            if out.contains(where: { $0.q == question }) {
                throw ProbeTurn.SetupError(description: "вопрос в наборе дважды: \(question)")
            }
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
            // порог с отступом молча снимал гейт (out3, DS M8)
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("max_false_refusal:") || line.hasPrefix("min_abstain:") {
                let (key, value) = (line.prefix(while: { $0 != ":" }),
                                    scalar(line.drop(while: { $0 != ":" }).dropFirst()))
                // кривой порог молча снял бы гейт
                if key == "max_false_refusal", let n = Int(value) { gate.maxFalseRefusal = n }
                else if key == "min_abstain", let x = Double(value) { gate.minAbstain = x }
                else { throw ProbeTurn.SetupError(description: "порог набора не разобрался: \(line)") }
            } else if line.hasPrefix("- q:") {
                try flush()
                q = scalar(line.dropFirst(4))
            } else if line.hasPrefix("must:"), q != nil {
                let inside = line.drop(while: { $0 != "[" }).dropFirst().prefix(while: { $0 != "]" })
                must = inside.split(separator: ",").map { unquote(String($0)) }
                    .filter { !$0.isEmpty }
            } else if line.hasPrefix("cat:"), q != nil {
                otherGraph = scalar(line.dropFirst("cat:".count)) == "other_graph"
            } else if line.hasPrefix("abstain:"), q != nil {
                // «True», «yes» молча снимали бы вопрос с воздержания (out2, DS I6)
                switch scalar(line.dropFirst("abstain:".count)) {
                case "true": abstain = true
                case "false": abstain = false
                default: throw ProbeTurn.SetupError(description: "abstain — только true или false: \(line)")
                }
            }
        }
        try flush()
        return (out, gate)
    }

    /// Значение поля: строка в кавычках — до закрывающей, иначе до « #»
    /// (комментарий YAML уходил в текст вопроса, out2, Sonnet I2).
    private func scalar(_ s: Substring) -> String {
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        if let q = t.first, q == "\"" || q == "'" {
            return String(t.dropFirst().prefix(while: { $0 != q }))
        }
        let bare = t.range(of: " #").map { String(t[..<$0.lowerBound]) } ?? t
        return bare.trimmingCharacters(in: .whitespaces)
    }

    private func unquote(_ s: String) -> String {
        s.trimmingCharacters(in: .whitespaces)
            .trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
    }
}
