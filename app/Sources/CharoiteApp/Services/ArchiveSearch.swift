import Foundation

/// Локальный поиск по графу встреч — прямо по markdown-файлам, без облака.
///
/// Ранжирование v2 (июль 2026), зеркало серверного vault_search:
/// стемминг русских окончаний (одно совпадение покрывает «встреча/встречах»),
/// IDF (редкое слово запроса весит больше частого), покрытие запроса (файл со
/// всеми словами выше файла с одним частым), свежесть (дата из имени файла),
/// демпфер сырых стенограмм (дистилляты графа — Ядра/Системы/инструкции —
/// при равной релевантности выше), разнообразие выдачи (одна встреча не
/// занимает все слоты своими заметкой+архивом+стенограммой).
///
/// Если поднят локальный brain-сервер (порт 8100, опциональный компаньон) —
/// поиск уходит туда: там тот же алгоритм плюс семантический слой bge-m3.
/// Всё на этой машине, ничего никуда не уходит.
enum ArchiveSearch {
    private static let stop: Set<String> = [
        "что", "как", "где", "когда", "это", "нас", "наш", "наша", "наши",
        "есть", "про", "для", "или", "чем", "кто", "было", "быть", "решили",
    ]

    /// Русские окончания, от длинных к коротким — обрезаем первое подошедшее,
    /// если остаток ≥ 4 символов. Стем ищется как подстрока.
    private static let suffixes = [
        "иями", "ями", "ами", "иях", "иям", "ыми", "ими", "ому", "ему",
        "ого", "его", "ует", "уют", "ают", "яют", "ешь", "ете", "лся",
        "лась", "лись", "ться", "ах", "ях", "ам", "ям", "ой", "ей", "ою",
        "ею", "ия", "ие", "ии", "ию", "ых", "их", "ым", "им", "ая", "яя",
        "ое", "ее", "ую", "юю", "ые", "ов", "ев", "ом", "ем", "ет", "ит",
        "ат", "ят", "ла", "ло", "ли", "ть", "ы", "и", "а", "я", "о", "е",
        "у", "ю", "ь",
    ]

    static func norm(_ s: String) -> String {
        s.lowercased().replacingOccurrences(of: "ё", with: "е")
    }

    /// Английские окончания — porter-lite: ing/ed/es/s (остаток ≥ 4).
    private static let enSuffixes = ["ing", "ed", "es", "s"]

    static func stem(_ word: String) -> String {
        let w = norm(word)
        guard w.count > 4 else { return w }
        let isLatin = w.unicodeScalars.first.map { CharacterSet.lowercaseLetters
            .contains($0) && $0.isASCII } ?? false
        let table = isLatin ? enSuffixes : suffixes
        for suf in table where w.hasSuffix(suf) && w.count - suf.count >= 4 {
            let cut = String(w.dropLast(suf.count))
            // Один срез разводит пары ед/мн: meetings→meeting, meeting→meet.
            // Латиницу дожимаем до неподвижной точки — страховка длины (≥4)
            // остаётся на каждом шаге, глубина ограничена длиной слова.
            // Русские окончания каскадом не наслаиваются — им один срез.
            return isLatin ? stem(cut) : cut
        }
        return w
    }

    /// Маркер слабых совпадений от brain-сервера: UI показывает честную
    /// плашку, синтез отвечает «в архиве нет», а не сочиняет.
    static let lowConfidenceMarker = "⚠"

    /// Топ-`limit` файлов графа со сниппетами ~`snippet` знаков.
    /// Сначала пробуем brain (:8100, гибрид с семантикой), иначе — локально.
    static func search(query: String, limit: Int = 5, snippet: Int = 1200) async -> String {
        if let viaBrain = await brainSearch(query: query, limit: limit, snippet: snippet) {
            return viaBrain
        }
        return await localSearch(query: query, limit: limit, snippet: snippet)
    }

    /// Поиск через локальный brain-сервер; nil — сервер не поднят/не ответил.
    private static func brainSearch(query: String, limit: Int, snippet: Int) async -> String? {
        guard let graph = AppSettings.graphDir,
              let url = URL(string: "http://127.0.0.1:8100/vault_search") else { return nil }
        // brain ищет по всему vault: сузим до папки графа (её имя — подпапка)
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 4
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "query": query, "folder": graph.lastPathComponent,
            "limit": limit, "snippet_chars": snippet,
        ] as [String: Any])
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]
        guard let (data, resp) = try? await URLSession(configuration: cfg).data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let text = obj["text"] as? String, !text.isEmpty else { return nil }
        if text.hasPrefix("Ничего не найдено") { return "" }
        // граф вне vault brain-сервера (демо, другой диск) — честный фолбэк
        // на локальный поиск, а не сообщение об ошибке в качестве «сырья»
        if text.hasPrefix("Папка не найдена") || text.hasPrefix("Недопустимый путь") { return nil }
        // тело без строки-шапки «Найдено в vault (N из M):», маркер «⚠» сохраняем
        let lowConf = text.hasPrefix(lowConfidenceMarker)
        if let range = text.range(of: "\n\n") {
            let body = String(text[range.upperBound...])
            return lowConf ? lowConfidenceMarker + body : body
        }
        return text
    }

    private struct Hit {
        let score: Double
        let rel: String
        let block: String
    }

    /// Локальный поиск с ранжированием v2: лексика + семантика (bge-m3
    /// через Ollama, если индекс прогрет) через RRF; гейт честности при
    /// слабых обоих сигналах. Работает и вовсе без серверов — тогда чисто
    /// лексически.
    static func localSearch(query: String, limit: Int = 5, snippet: Int = 1200,
                            root: URL? = nil) async -> String {
        guard var graph = root ?? AppSettings.graphDir,
              FileManager.default.fileExists(atPath: graph.path) else { return "" }
        // канонизация: /var/… и /private/var/… — один каталог через симлинк;
        // enumerator отдаёт канонический путь, и строковый срез graph.path
        // иначе оставляет мусорный префикс в rel — ключи индекса расходятся
        graph = graph.resolvingSymlinksInPath()
        let words = query
            .components(separatedBy: CharacterSet.alphanumerics.inverted)
            .filter { $0.count >= 3 && !stop.contains(norm($0)) }
        guard !words.isEmpty else { return "" }
        var needles: [String] = []
        for w in words.map(stem) where !needles.contains(w) { needles.append(w) }

        // проход 1: читаем весь граф. Лексические попадания — фильтром ниже,
        // но файлы без совпадений не выбрасываются: семантика ищет по всему
        // графу. Пересечение с лексикой запирало её в пересортировку уже
        // найденного, а её главная работа — словарный разрыв: вопрос задан
        // словами, которых в файле нет.
        // dateTs — дата для свежести (у встреч/daily — из имени файла);
        // mtime — настоящее время правки, только для инвалидации индекса
        // swiftlint:disable:next large_tuple — внутренний счётный кортеж, не API
        var all: [(text: String, rel: String, tHits: [Int], pHits: [Int], dateTs: Double, mtime: Double)] = []  // rel — ключ RRF
        let keys: [URLResourceKey] = [.isRegularFileKey, .contentModificationDateKey]
        guard let walker = FileManager.default.enumerator(
            at: graph, includingPropertiesForKeys: keys,
            options: [.skipsHiddenFiles]) else { return "" }
        for case let url as URL in walker {
            guard url.pathExtension == "md",
                  let text = try? String(contentsOf: url, encoding: .utf8) else { continue }
            let low = norm(text)
            let canon = url.resolvingSymlinksInPath().path
            let rel = canon.hasPrefix(graph.path + "/")
                ? String(canon.dropFirst(graph.path.count + 1))
                : url.lastPathComponent
            let relLow = norm(rel)
            let tHits = needles.map { countOccurrences(of: $0, in: low) }
            let pHits = needles.map { countOccurrences(of: $0, in: relLow) }
            let rv = try? url.resourceValues(forKeys: [.contentModificationDateKey])
            let mtime = rv?.contentModificationDate?.timeIntervalSince1970 ?? 0
            all.append((text, rel, tHits, pHits, fileDate(rel: rel, mtime: mtime), mtime))
        }
        guard !all.isEmpty else { return "" }
        let files = all.filter { f in
            f.tHits.contains(where: { $0 > 0 }) || f.pHits.contains(where: { $0 > 0 })
        }

        // проход 2: IDF + скоринг
        let nDocs = max(1, files.count)
        let idfs: [Double] = (0..<needles.count).map { i in
            let df = files.filter { $0.tHits[i] > 0 || $0.pHits[i] > 0 }.count
            return log(1.0 + Double(max(0, nDocs - df + 1)) / Double(df + 1))
        }
        var hits: [Hit] = []
        for f in files {
            var score = 0.0
            for i in 0..<needles.count {
                if f.tHits[i] > 0 { score += idfs[i] * (1.0 + log(1.0 + Double(f.tHits[i]))) }
                score += 3.0 * idfs[i] * Double(f.pHits[i])
            }
            let matched = (0..<needles.count).filter { f.tHits[$0] > 0 || f.pHits[$0] > 0 }.count
            score *= pow(Double(matched) / Double(needles.count), 0.5)   // покрытие запроса
            score *= recency(f.dateTs)                                    // свежесть
            score *= rawDampener(f.rel)                                   // сырьё ниже дистиллятов
            let frag = bestWindow(text: f.text, needles: needles, span: snippet)
            guard !frag.isEmpty else { continue }
            hits.append(Hit(score: score, rel: f.rel, block: "• \(f.rel)\n  …\(frag)…"))
        }

        var bestCov = 0.0
        for f in files {
            let matched = (0..<needles.count).filter { f.tHits[$0] > 0 || f.pHits[$0] > 0 }.count
            bestCov = max(bestCov, Double(matched) / Double(needles.count))
        }

        // ─ Семантика: RRF с лексикой (веса 1.0/0.7), как в brain-компаньоне ─
        // Кандидаты — ВЕСЬ граф, не только лексические попадания: иначе ветка
        // объединения в слиянии ниже — мёртвый код, а словарный разрыв
        // (вопрос другими словами) не закрывается никогда.
        let byPath = Dictionary(uniqueKeysWithValues: all.map {
            ($0.rel, (text: $0.text, dateTs: $0.dateTs))
        })
        let allPaths = Set(all.map(\.rel))
        let sem = await SemanticIndex.shared.similar(to: query, within: allPaths,
                                                     limit: max(limit * 4, 20))
        let bestSim = sem.first?.1 ?? 0
        var semHits: [Hit] = []
        for (rel, sim) in sem {
            guard let f = byPath[rel] else { continue }
            let frag = bestWindow(text: f.text, needles: needles, span: snippet)
            let block = frag.isEmpty
                ? "• \(rel)\n  …\(String(f.text.prefix(snippet)).split(whereSeparator: \.isNewline).joined(separator: " "))…"
                : "• \(rel)\n  …\(frag)…"
            semHits.append(Hit(score: sim * recency(f.dateTs) * rawDampener(rel),
                               rel: rel, block: block))
        }
        var fused: [String: (score: Double, hit: Hit)] = [:]
        for (rank, h) in hits.sorted(by: { $0.score > $1.score }).enumerated() {
            fused[h.rel] = (1.0 / (60.0 + Double(rank) + 1), h)
        }
        for (rank, h) in semHits.enumerated() {
            let add = 0.7 / (60.0 + Double(rank) + 1)
            if let cur = fused[h.rel] { fused[h.rel] = (cur.score + add, cur.hit) }
            else { fused[h.rel] = (add, h) }
        }
        let merged = fused.values.map { Hit(score: $0.score, rel: $0.hit.rel, block: $0.hit.block) }

        // фоновая доиндексация изменившихся файлов — не задерживает ответ;
        // весь граф, а не только попавшееся текущему запросу. В снапшоте —
        // НАСТОЯЩИЙ mtime: dateTs у встреч — дата из имени, она не меняется
        // при правке, и правленый файл никогда не переиндексировался бы
        let snapshot = all.map { (path: $0.rel, mtime: $0.mtime, text: $0.text) }
        Task.detached(priority: .background) {
            await SemanticIndex.shared.refresh(files: snapshot)
        }

        // разнообразие: одна встреча (заметка+архив+стенограмма) ≤ 1-2 слота
        var shown = diversify(merged, limit: limit)
        // Слот для семантики: RRF-вес 0.7/(60+rank) не обгоняет лексический
        // 1/(60+rank) при rank<26 — при ≥limit лексических хитов сильная
        // семантическая находка математически не попадала в выдачу. Сильная
        // (≥ порога гейта) — показывается всегда; заодно честен и сам гейт:
        // bestSim, снявший пометку «⚠», виден пользователю, а не лежит
        // где-то в графе невидимым оправданием.
        if let top = sem.first, top.1 >= 0.47,
           !shown.contains(where: { $0.rel == top.0 }),
           let topHit = semHits.first(where: { $0.rel == top.0 }) {
            if shown.count >= limit { shown.removeLast() }
            shown.append(topHit)
        }
        let body = shown.map(\.block).joined(separator: "\n\n")
        // гейт честности: оба сигнала слабые → пометка, синтез не сочиняет
        if bestSim < 0.47 && bestCov < 0.67 && !body.isEmpty {
            return lowConfidenceMarker + body
        }
        return body
    }

    // MARK: - Ранжирование

    private static func countOccurrences(of needle: String, in hay: String) -> Int {
        guard !needle.isEmpty else { return 0 }
        var count = 0
        var idx = hay.startIndex
        while let r = hay.range(of: needle, range: idx..<hay.endIndex) {
            count += 1
            idx = r.upperBound
        }
        return count
    }

    /// Дата файла: YYYY-MM-DD из имени (встречи/daily) точнее iCloud-mtime.
    private static func fileDate(rel: String, mtime: Double) -> Double {
        if let r = rel.range(of: #"20\d{2}-\d{2}-\d{2}"#, options: .regularExpression) {
            let fmt = DateFormatter()
            fmt.dateFormat = "yyyy-MM-dd"
            fmt.timeZone = TimeZone(identifier: "UTC")
            if let d = fmt.date(from: String(rel[r])) { return d.timeIntervalSince1970 }
        }
        return mtime
    }

    /// Свежесть: 1.0 сегодня → 0.5 на бесконечности, полураспад 90 дней.
    private static func recency(_ ts: Double) -> Double {
        guard ts > 0 else { return 1.0 }
        let age = max(0, Date().timeIntervalSince1970 - ts) / 86400.0
        return 0.5 + 0.5 * pow(2.0, -age / 90.0)
    }

    /// Сырые стенограммы фонят частотами и дублируют дистилляты графа.
    private static func rawDampener(_ rel: String) -> Double {
        let low = norm(rel)
        return (low.contains("стенограмм") || low.contains("_live.md")
                || low.contains("transcript")) ? 0.75 : 1.0
    }

    /// Окно вокруг места с максимумом РАЗНЫХ слов запроса: первое совпадение
    /// часто в шапке, а ответ — в середине файла. Короткий файл — целиком.
    private static func bestWindow(text: String, needles: [String], span: Int) -> String {
        if text.count <= span + span / 2 {
            return text.split(whereSeparator: \.isNewline).joined(separator: " ")
        }
        let low = norm(text)
        var positions: [String.Index] = []
        for n in needles {
            var idx = low.startIndex
            while let r = low.range(of: n, range: idx..<low.endIndex) {
                positions.append(r.lowerBound)
                idx = r.upperBound
                if positions.count > 60 { break }
            }
        }
        guard !positions.isEmpty else { return "" }
        var best = positions[0]
        var bestKinds = -1
        for p in positions {
            let end = low.index(p, offsetBy: span, limitedBy: low.endIndex) ?? low.endIndex
            let window = low[p..<end]
            let kinds = needles.filter { window.contains($0) }.count
            if kinds > bestKinds { bestKinds = kinds; best = p }
        }
        let s = low.index(best, offsetBy: -150, limitedBy: low.startIndex) ?? low.startIndex
        let e = low.index(best, offsetBy: span, limitedBy: low.endIndex) ?? low.endIndex
        // офсеты в оригинале: norm не меняет длину строки
        let os = text.index(text.startIndex, offsetBy: low.distance(from: low.startIndex, to: s))
        let oe = text.index(text.startIndex, offsetBy: low.distance(from: low.startIndex, to: e))
        return text[os..<oe].split(whereSeparator: \.isNewline).joined(separator: " ")
    }

    /// Ключ встречи (дата[_время] в пути) — для разнообразия выдачи.
    private static func meetingKey(_ rel: String) -> String? {
        guard let r = rel.range(of: #"20\d{2}-\d{2}-\d{2}[_ ]?\d{0,4}"#,
                                options: .regularExpression) else { return nil }
        return String(rel[r])
    }

    private static func diversify(_ hits: [Hit], limit: Int) -> [Hit] {
        var pool = hits.sorted { $0.score > $1.score }
        var picked: [Hit] = []
        var used: [String: Int] = [:]
        while !pool.isEmpty && picked.count < limit {
            var bestI = 0
            var bestEff = -1.0
            for (i, h) in pool.enumerated() {
                let dupes = meetingKey(h.rel).flatMap { used[$0] } ?? 0
                let eff = h.score * pow(0.45, Double(dupes))
                if eff > bestEff { bestEff = eff; bestI = i }
            }
            let h = pool.remove(at: bestI)
            if let k = meetingKey(h.rel) { used[k, default: 0] += 1 }
            picked.append(h)
        }
        return picked
    }

    /// Вопрос к Ollama с построчным стримом: onToken зовётся на каждый кусок,
    /// возврат — полный текст. Стрим — живость главной функции: первые слова
    /// через ~1с вместо десятков секунд молчания на длинных ответах.
    static func ask(question: String, system: String, model: String,
                    ollama: String, onToken: ((String) -> Void)? = nil) async -> String {
        guard let url = URL(string: ollama + "/api/chat") else { return "" }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 180
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "model": model,
            "messages": [["role": "system", "content": system],
                         ["role": "user", "content": question]],
            "stream": true,
            "think": false,
            "keep_alive": "30m",
            "options": ["temperature": 0.3, "num_ctx": 8192, "num_predict": 1024],
        ] as [String: Any])
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]   // локальный вызов мимо системного прокси
        let session = URLSession(configuration: cfg)
        guard let (bytes, _) = try? await session.bytes(for: req) else { return "" }
        var full = ""
        do {
            for try await line in bytes.lines {
                guard let data = line.data(using: .utf8),
                      let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
                else { continue }
                if let msg = obj["message"] as? [String: Any],
                   let chunk = msg["content"] as? String, !chunk.isEmpty {
                    full += chunk
                    onToken?(full)
                }
                if obj["done"] as? Bool == true { break }
            }
        } catch { return full }
        return full
    }
}
