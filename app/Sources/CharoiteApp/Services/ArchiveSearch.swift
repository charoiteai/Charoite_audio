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
    /// Служебные слова: в иглы не идут и в покрытие запроса не считаются.
    ///
    /// Английского списка тут не было вовсе, и это ломало продукт на его же
    /// демо-графе: «what did we decide about the payment provider?» давало
    /// иглы what/did/the — покрытие 0.57 вместо 1.0, вопрос получал пометку
    /// «возможно, в архиве этого нет», а синтез — инструкцию не доверять
    /// найденному. То есть канонический вопрос из README продукт сам объявлял
    /// безответным.
    private static let stop: Set<String> = [
        "что", "как", "где", "когда", "это", "нас", "наш", "наша", "наши",
        "есть", "про", "для", "или", "чем", "кто", "было", "быть", "решили",
        "какие", "какой", "какая", "сейчас", "статус", "тогда", "тот", "там",
        "what", "who", "when", "where", "why", "how", "the", "and", "for",
        "are", "was", "were", "did", "does", "about", "with", "from", "that",
        "this", "our", "their", "there", "have", "has", "had", "you", "your",
        "current", "status", "decide", "decided",
    ]

    /// Служебное ли слово (нормализуется так же, как иглы запроса).
    static func isStopWord(_ word: String) -> Bool { stop.contains(norm(word)) }

    /// Русские окончания, от длинных к коротким — обрезаем первое подошедшее,
    /// если остаток ≥ 4 символов. Стем ищется как подстрока.
    // Порядок значим: длинные окончания режутся раньше коротких.
    // «й» и адъективные «ый/ий» пришлось дописать — без них стем зависел от
    // формы, в которой человек набрал слово: «решений» не сводилось к «решен»
    // и не находило файл со словом «решение», «платежный» не находило
    // «платёжного». Поиск при этом молчал, и выглядело это как «архив пуст».
    private static let suffixes = [
        "иями", "ями", "ами", "иях", "иям", "ыми", "ими", "ому", "ему",
        "ого", "его", "ует", "уют", "ают", "яют", "ешь", "ете", "лся",
        "лась", "лись", "ться", "ый", "ий", "ах", "ях", "ам", "ям", "ой",
        "ей", "ою", "ею", "ия", "ие", "ии", "ию", "ых", "их", "ым", "им",
        "ая", "яя", "ое", "ее", "ую", "юю", "ые", "ов", "ев", "ом", "ем",
        "ет", "ит", "ат", "ят", "ла", "ло", "ли", "ть", "ы", "и", "а", "я",
        "о", "е", "у", "ю", "ь", "й",
    ]

    static func norm(_ s: String) -> String {
        s.lowercased().replacingOccurrences(of: "ё", with: "е")
    }

    /// Английские окончания — porter-lite: ing/ed/es/s (остаток ≥ 4).
    private static let enSuffixes = ["ing", "ed", "es", "s"]

    /// Иероглифические куски запроса — скользящими биграммами.
    ///
    /// В китайском, японском и корейском слова не отделяются пробелами, и
    /// `CharacterSet.alphanumerics` их не режет: вопрос 支付服务商最后定了哪一家？
    /// приходил сюда ОДНИМ «словом», которого в тексте графа нет, и выдача
    /// выходила пустой. Пользователь при этом видит не ошибку, а «в архиве
    /// ничего нет» — худший вид отказа. Биграммы (支付服务商 → 支付, 付服,
    /// 服务, 务商) — стандартный приём для языков без пробелов.
    static func cjkGrams(_ text: String) -> [String] {
        var out: [String] = []
        var run: [Character] = []
        func flush() {
            if run.count == 1 {
                out.append(String(run[0]))
            } else if run.count > 1 {
                for i in 0..<(run.count - 1) { out.append(String(run[i...(i + 1)])) }
            }
            run.removeAll()
        }
        for ch in text {
            if ch.unicodeScalars.allSatisfy(isCJK) { run.append(ch) } else { flush() }
        }
        flush()
        return out
    }

    private static func isCJK(_ scalar: Unicode.Scalar) -> Bool {
        switch scalar.value {
        case 0x4E00...0x9FFF,        // китайский (основной блок)
             0x3400...0x4DBF,        // расширение A
             0xF900...0xFAFF,        // совместимость (иероглифы из старых кодировок)
             0x3040...0x30FF,        // японские каны
             0xFF66...0xFF9F,        // полуширинная катакана
             0xAC00...0xD7AF,        // корейский хангыль
             0x20000...0x2EE5F,      // расширения B–F и I (редкие иероглифы)
             0x2F800...0x2FA1F,      // совместимость, дополнение
             0x30000...0x323AF:      // расширения G и H — отдельный остров
            return true
        default:
            return false
        }
    }

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
    /// CHAROITE_GRAPH_DIR (демо/тесты) — строго файловый поиск: brain этой
    /// машины индексирует ДРУГОЙ граф и молча отвечал бы не по подменённому.
    static func search(query: String, limit: Int = 5, snippet: Int = 1200,
                       budget: Int = defaultBudget) async -> String {
        let graphOverridden = AppSettings.graphDirEnvNames.contains { name in
            !(ProcessInfo.processInfo.environment[name] ?? "")
                .trimmingCharacters(in: .whitespaces).isEmpty
        }
        if !graphOverridden,
           let viaBrain = await brainSearch(query: query, limit: limit, snippet: snippet) {
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

    struct Hit {
        let score: Double
        let rel: String
        let block: String
    }

    /// Локальный поиск с ранжированием v2: лексика + семантика (bge-m3
    /// через Ollama, если индекс прогрет) через RRF; гейт честности при
    /// слабых обоих сигналах. Работает и вовсе без серверов — тогда чисто
    /// лексически.
    static func localSearch(query: String, limit: Int = 5, snippet: Int = 1200,
                            budget: Int = defaultBudget,
                            root: URL? = nil) async -> String {
        guard var graph = root ?? AppSettings.graphDir,
              FileManager.default.fileExists(atPath: graph.path) else { return "" }
        // канонизация: /var/… и /private/var/… — один каталог через симлинк;
        // enumerator отдаёт канонический путь, и строковый срез graph.path
        // иначе оставляет мусорный префикс в rel — ключи индекса расходятся
        graph = graph.resolvingSymlinksInPath()
        let grams = cjkGrams(query)
        let words = query
            .components(separatedBy: CharacterSet.alphanumerics.inverted)
            // Слово целиком из иероглифов заменяют его биграммы: как игла оно
            // бесполезно (в тексте такой фразы нет), а в покрытии запроса
            // весит наравне с настоящими словами и портит скоринг.
            .filter { $0.count >= 3 && !stop.contains(norm($0)) && cjkGrams($0).isEmpty }
        guard !words.isEmpty || !grams.isEmpty else { return "" }
        var needles: [String] = []
        for w in words.map(stem) where !needles.contains(w) { needles.append(w) }
        for g in grams where !needles.contains(g) { needles.append(g) }
        // Байтовые копии игл считаем ОДИН раз на запрос, а не на каждый файл.
        let needleBytes = needles.map { Array($0.utf8) }

        // проход 1: читаем весь граф. Лексические попадания — фильтром ниже,
        // но файлы без совпадений не выбрасываются: семантика ищет по всему
        // графу. Пересечение с лексикой запирало её в пересортировку уже
        // найденного, а её главная работа — словарный разрыв: вопрос задан
        // словами, которых в файле нет.
        // dateTs — дата для свежести (у встреч/daily — из имени файла);
        // mtime — настоящее время правки, только для инвалидации индекса
        // Внутренний счётный кортеж, не API.
        // swiftlint:disable:next large_tuple
        var all: [(text: String, rel: String, tHits: [Int], pHits: [Int], dateTs: Double, mtime: Double)] = []  // rel — ключ RRF
        let keys: [URLResourceKey] = [.isRegularFileKey, .contentModificationDateKey]
        // БЕЗ .skipsHiddenFiles — намеренно.
        //
        // iCloud метит элементы контейнера флагом UF_HIDDEN (это уже ловили
        // 20.07: Finder показывал архивную папку пустой). Флаг ложится и на
        // папки графа, а .skipsHiddenFiles пропускает их молча. Замер на
        // рабочем графе: обходчик видел 546 файлов из 1172 — целиком пропали
        // «Люди», «Системы», «Встречи» и почти вся «Документация», то есть
        // сердце графа. Поиск отвечал «в памяти этого нет» про людей, с
        // которыми были встречи на этой неделе.
        //
        // Скрытое по НАМЕРЕНИЮ (.obsidian, .trash, .git) отсекаем по имени —
        // это надёжнее флага, который ставит не пользователь.
        guard let walker = FileManager.default.enumerator(
            at: graph, includingPropertiesForKeys: keys) else { return "" }
        // Конвейер намеренно кладёт документы встречи ДВАЖДЫ: оригинал в
        // «Документация/Стенограммы встреч», побайтовая копия — в
        // «Встречи-архив/<дата — название>», чтобы папку можно было открыть
        // из Finder. Для человека это удобно, для поиска — двойной счёт:
        // на реальном графе 214 групп дублей, 37% объёма. Обе копии читались,
        // индексировались семантикой и попадали в выдачу как разные
        // источники, то есть модель получала один и тот же текст дважды и
        // тратила на повтор контекст.
        //
        // Оригинал выигрывает: сортируем так, чтобы архивные копии шли
        // последними, и первый увиденный текст остаётся единственным.
        let urls = walker.compactMap { $0 as? URL }
            .filter { $0.pathExtension == "md" }
            .filter { url in
                // Точка в начале любого компонента пути = служебное:
                // .obsidian/, .trash/, .git/, .DS_Store и подобное.
                !url.pathComponents.contains { $0.hasPrefix(".") && $0.count > 1 }
            }
            .sorted { a, b in
                let aArch = a.path.contains("/Встречи-архив/")
                let bArch = b.path.contains("/Встречи-архив/")
                return aArch == bArch ? a.path < b.path : !aArch
            }
        var seenContent = Set<Int>()
        var liveKeys = Set<String>()
        for url in urls {
            let canon = url.resolvingSymlinksInPath().path
            let rel = canon.hasPrefix(graph.path + "/")
                ? String(canon.dropFirst(graph.path.count + 1))
                : url.lastPathComponent
            let rv = try? url.resourceValues(forKeys: [.contentModificationDateKey])
            let mtime = rv?.contentModificationDate?.timeIntervalSince1970 ?? 0
            liveKeys.insert(rel)
            // Кэш по mtime: правка в Obsidian видна сразу, а неизменившийся
            // файл не читается и не нормализуется повторно. В чате с памятью
            // поиск идёт на каждое сообщение — без кэша диалог из десяти
            // реплик перечитывал весь граф десять раз.
            guard let cached = await GraphCache.shared.text(at: url, key: rel,
                                                           mtime: mtime, normalize: norm)
            else { continue }
            // Хеш содержимого, а не пути: копия отличается именем и папкой.
            guard seenContent.insert(cached.text.hashValue).inserted else { continue }
            let relBytes = Array(norm(rel).utf8)
            let tHits = needleBytes.map { countOccurrences(of: $0, in: cached.bytes) }
            let pHits = needleBytes.map { countOccurrences(of: $0, in: relBytes) }
            all.append((cached.text, rel, tHits, pHits, fileDate(rel: rel, mtime: mtime), mtime))
        }
        // Переименованные и удалённые файлы не должны занимать память вечно.
        await GraphCache.shared.retain(keys: liveKeys)
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
        // Сначала СКОРЫ для всех совпавших — это дёшево, только арифметика.
        var scored: [(score: Double, rel: String, text: String)] = []
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
            scored.append((score, f.rel, f.text))
        }
        // Сниппеты — ТОЛЬКО для кандидатов на выдачу. Раньше окно искалось у
        // КАЖДОГО совпавшего файла, то есть у сотен документов, из которых в
        // ответ попадут пять. На рабочем графе это была основная статья
        // расхода поиска. Берём с запасом: дальше слияние с семантикой и
        // разнообразие могут отбросить часть кандидатов.
        scored.sort { $0.score > $1.score }
        var hits: [Hit] = []
        for cand in scored.prefix(max(limit * 4, 20)) {
            let frag = bestWindow(text: cand.text, needles: needles, span: snippet)
            guard !frag.isEmpty else { continue }
            hits.append(Hit(score: cand.score, rel: cand.rel,
                            block: "• \(cand.rel)\n  …\(frag)…"))
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
        // uniquingKeysWith, а не uniqueKeysWithValues: ключ `rel` не гарантированно
        // уникален (симлинк наружу vault теряет префикс графа и схлопывается в
        // lastPathComponent), а дубликат в этом инициализаторе — не ошибка, а
        // fatalError. Два `README.md` из разных подпапок роняли приложение на
        // любом поиске, включая фоновый прогрев при старте.
        let byPath = Dictionary(all.map { ($0.rel, (text: $0.text, dateTs: $0.dateTs)) },
                                uniquingKeysWith: { first, _ in first })
        let allPaths = Set(all.map(\.rel))
        let sem = await SemanticIndex.shared.similar(to: query, within: allPaths,
                                                     limit: max(limit * 4, 20))
        let bestSim = sem.first?.score ?? 0
        var semHits: [Hit] = []
        for (rel, sim, chunkSnippet) in sem {
            guard let f = byPath[rel] else { continue }
            // Порядок предпочтений для сниппета семантического хита:
            // 1) окно вокруг лексических игл — если они в файле есть;
            // 2) НАЙДЕННЫЙ БЛОК — попадание было именно в него;
            // 3) начало файла — последняя надежда.
            // Раньше пунктов 2 не существовало, и семантическое попадание в
            // середину трёхчасовой стенограммы показывало её приветствие:
            // модель получала «все собрались, слышно меня?» вместо решения,
            // ради которого файл и был найден.
            let frag = bestWindow(text: f.text, needles: needles, span: snippet)
            let body: String
            if !frag.isEmpty {
                body = frag
            } else if !chunkSnippet.isEmpty {
                body = String(chunkSnippet.prefix(snippet))
                    .split(whereSeparator: \.isNewline).joined(separator: " ")
            } else {
                body = String(f.text.prefix(snippet))
                    .split(whereSeparator: \.isNewline).joined(separator: " ")
            }
            semHits.append(Hit(score: sim * recency(f.dateTs) * rawDampener(rel),
                               rel: rel, block: "• \(rel)\n  …\(body)…"))
        }
        var fused: [String: (score: Double, hit: Hit)] = [:]
        for (rank, h) in hits.sorted(by: { $0.score > $1.score }).enumerated() {
            fused[h.rel] = (1.0 / (60.0 + Double(rank) + 1), h)
        }
        // Сортировка по собственному скору обязательна: в него уже вложены
        // свежесть и демпфер сырых стенограмм, а RRF смотрит только на РАНГ.
        // Без неё порядок задавался чистым косинусом, и трёхлетняя стенограмма
        // (cos 0.63) забирала семантический слот у свежего ядра (cos 0.61) —
        // притом что со всеми множителями ядро выигрывало втрое.
        for (rank, h) in semHits.sorted(by: { $0.score > $1.score }).enumerated() {
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
            // pruneMissing: снимок здесь — ВЕСЬ граф, поэтому чего в нём нет,
            // того больше нет и на диске. Так забытая встреча уходит и из
            // индекса, а не живёт там превью-блоками (аудит 16.08).
            await SemanticIndex.shared.refresh(files: snapshot, pruneMissing: true)
        }

        // разнообразие: одна встреча (заметка+архив+стенограмма) ≤ 1-2 слота
        var shown = diversify(merged, limit: limit)
        // Слот для семантики: RRF-вес 0.7/(60+rank) не обгоняет лексический
        // 1/(60+rank) при rank<26 — при ≥limit лексических хитов сильная
        // семантическая находка математически не попадала в выдачу. Сильная
        // (≥ порога гейта) — показывается всегда; заодно честен и сам гейт:
        // bestSim, снявший пометку «⚠», виден пользователю, а не лежит
        // где-то в графе невидимым оправданием.
        if let top = sem.first, top.score >= 0.47,
           !shown.contains(where: { $0.rel == top.path }),
           let topHit = semHits.first(where: { $0.rel == top.path }) {
            if shown.count >= limit { shown.removeLast() }
            shown.append(topHit)
        }
        let body = packContext(shown, budget: budget)
        // Гейт честности: оба сигнала слабые → пометка, синтез не сочиняет.
        // Порог 0.66, а не 0.67: «две иглы из трёх» — это 0.6667, и с прежним
        // числом правило требовало на самом деле три из трёх.
        if bestSim < 0.47 && bestCov < 0.66 && !body.isEmpty {
            return lowConfidenceMarker + body
        }
        return body
    }

    // MARK: - Бюджет контекста

    /// Сколько знаков выдачи уходит модели по умолчанию.
    ///
    /// Окно локальной модели (num_ctx 32768 ≈ 100 000 знаков) — не цель, в
    /// которую надо целиться. При заполнении больше половины окна модель
    /// начинает терять начало контекста, а лишний найденный файл вредит
    /// сильнее, чем помогает: дистракторы бьют по точности нелинейно. Плюс
    /// у MoE-модели активны единицы миллиардов параметров, и длинный шумный
    /// контекст даётся ей тяжелее, чем плотному аналогу.
    static let defaultBudget = 6000

    /// Собрать выдачу под бюджет: потолок на источник и порядок против
    /// «потери середины».
    ///
    /// Три правила, каждое из наблюдаемого поведения длинных контекстов:
    /// • ни один источник не забирает больше 40% бюджета — иначе одна
    ///   трёхчасовая стенограмма вытесняет все остальные встречи, а ответ на
    ///   «что решили по X» почти всегда требует нескольких;
    /// • источник, которому досталось меньше 300 знаков, выбрасывается
    ///   целиком: огрызок не несёт смысла, но занимает место и путает;
    /// • лучший источник идёт первым, второй по силе — ПОСЛЕДНИМ. Внимание
    ///   модели распределено по краям контекста, и середина проседает; так
    ///   два сильнейших попадают в оба сильных места.
    static func packContext(_ hits: [Hit], budget: Int) -> String {
        guard budget > 0 else { return hits.map(\.block).joined(separator: "\n\n") }
        let perSource = max(600, budget * 2 / 5)
        var kept: [String] = []
        var spent = 0
        for hit in hits {
            let room = min(perSource, budget - spent)
            if room < 300 { break }
            let block = hit.block.count <= room
                ? hit.block
                : String(hit.block.prefix(room)) + "…"
            kept.append(block)
            spent += block.count + 2
        }
        guard kept.count > 2 else { return kept.joined(separator: "\n\n") }
        // [1-й, 3-й, 4-й, …, 2-й]
        let reordered = [kept[0]] + kept.dropFirst(2) + [kept[1]]
        return reordered.joined(separator: "\n\n")
    }

    // MARK: - Ранжирование

    private static func countOccurrences(of needle: String, in hay: String) -> Int {
        countOccurrences(of: Array(needle.utf8), in: Array(hay.utf8))
    }

    /// Шов для теста эквивалентности со строковым поиском.
    static func countOccurrencesForTests(of needle: String, in hay: String) -> Int {
        countOccurrences(of: needle, in: hay)
    }

    /// Побайтовый счётчик вхождений.
    ///
    /// String.range(of:) сравнивает с юникод-нормализацией — учитывает
    /// эквивалентность составных символов, чего нам не нужно: обе стороны уже
    /// приведены к нижнему регистру и «ё»→«е». На графе в тысячу файлов эта
    /// нормализация и была главной статьёй расхода поиска.
    private static func countOccurrences(of needle: [UInt8], in hay: [UInt8]) -> Int {
        guard !needle.isEmpty, hay.count >= needle.count else { return 0 }
        let first = needle[0]
        let last = hay.count - needle.count
        var count = 0
        var i = 0
        while i <= last {
            guard hay[i] == first else { i += 1; continue }
            var j = 1
            while j < needle.count, hay[i + j] == needle[j] { j += 1 }
            if j == needle.count {
                count += 1
                i += needle.count      // без перекрытий, как было
            } else {
                i += 1
            }
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
    /// Вес документа по его роли в конвейере.
    ///
    /// Конвейер производит из одной встречи несколько документов, и они очень
    /// разного качества как ответ на вопрос «что решили». Замер по рабочему
    /// графу показал, что демпфер видел только треть сырья:
    ///
    ///   стенограммы          290 файлов, 8.2 МБ — демпфер был
    ///   подсказки/hints       44 файла, 2.4 МБ — демпфера НЕ было
    ///   вопросы-ответы        49 файлов, 1.7 МБ — демпфера НЕ было
    ///   черновики             31 файл,  1.1 МБ — демпфера НЕ было
    ///   минутки/саммари       98 файлов, 0.4 МБ — приоритета НЕ было
    ///
    /// То есть 5.2 МБ сырых расшифровок конкурировали на равных с узлами
    /// графа, а минутки — готовая человеческая выжимка решений, ровно то, за
    /// чем приходят с вопросом, — не имели никакого преимущества.
    /// Шов для тестов: вес роли документа проверяется напрямую, а не через
    /// косвенный порядок выдачи — тот зависит ещё от IDF и покрытия запроса.
    static func roleWeightForTests(_ rel: String) -> Double { rawDampener(rel) }

    private static func rawDampener(_ rel: String) -> Double {
        let low = norm(rel)
        // Сырьё: длинное, дословное, с оговорками и повторами. Ценно для
        // цитаты, плохо как ответ.
        let raw = ["стенограмм", "_live.md", "transcript", "подсказки и ответы",
                   "_hints", "вопросы и ответы", "черновик"]
        if raw.contains(where: { low.contains($0) }) { return 0.7 }
        // Дистиллят: решения уже отобраны и сформулированы.
        let distilled = ["минутк", "_minutes", "саммари", "разбор", "ядра/", "_moc"]
        if distilled.contains(where: { low.contains($0) }) { return 1.15 }
        return 1.0
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
