import Foundation

/// Локальный поиск по графу встреч — прямо по markdown-файлам, без серверов.
///
/// Та же механика, что в scripts/memory_bench.py питон-части: слова запроса →
/// скоринг файлов (совпадение в пути весит больше) → жирные сниппеты для
/// RAG-синтеза. Всё на этой машине, ничего никуда не уходит.
enum ArchiveSearch {
    private static let stop: Set<String> = [
        "что", "как", "где", "когда", "это", "нас", "наш", "наша", "наши",
        "есть", "про", "для", "или", "чем", "кто", "было", "быть", "решили",
    ]

    static func norm(_ s: String) -> String {
        s.lowercased().replacingOccurrences(of: "ё", with: "е")
    }

    /// Топ-`limit` файлов графа со сниппетами ~`snippet` знаков после совпадения.
    static func search(query: String, limit: Int = 5, snippet: Int = 1200) -> String {
        guard let graph = AppSettings.graphDir,
              FileManager.default.fileExists(atPath: graph.path) else { return "" }
        let words = query
            .components(separatedBy: CharacterSet.alphanumerics.inverted)
            .filter { $0.count >= 3 && !stop.contains(norm($0)) }
        guard !words.isEmpty else { return "" }
        let needles = words.map(norm)

        var scored: [(Int, String)] = []
        let keys: [URLResourceKey] = [.isRegularFileKey]
        guard let walker = FileManager.default.enumerator(
            at: graph, includingPropertiesForKeys: keys,
            options: [.skipsHiddenFiles]) else { return "" }
        for case let url as URL in walker {
            guard url.pathExtension == "md",
                  let text = try? String(contentsOf: url, encoding: .utf8) else { continue }
            let low = norm(text)
            guard let first = needles.compactMap({ low.range(of: $0) }).min(by: { $0.lowerBound < $1.lowerBound })
            else { continue }
            let rel = url.path.replacingOccurrences(of: graph.path + "/", with: "")
            var score = needles.filter { low.contains($0) }.count
            score += 3 * needles.filter { norm(rel).contains($0) }.count
            // сниппет: от 150 знаков до совпадения до `snippet` после
            let startIdx = low.index(first.lowerBound, offsetBy: -150, limitedBy: low.startIndex) ?? low.startIndex
            let endIdx = low.index(first.upperBound, offsetBy: snippet, limitedBy: low.endIndex) ?? low.endIndex
            // срез из ОРИГИНАЛА (регистры/пунктуация): офсеты совпадают, norm не меняет длину
            let s = text.index(text.startIndex, offsetBy: low.distance(from: low.startIndex, to: startIdx))
            let e = text.index(text.startIndex, offsetBy: low.distance(from: low.startIndex, to: endIdx))
            let frag = text[s..<e].split(whereSeparator: \.isNewline).joined(separator: " ")
            scored.append((score, "• \(rel)\n  …\(frag)…"))
        }
        scored.sort { $0.0 > $1.0 }
        return scored.prefix(limit).map(\.1).joined(separator: "\n\n")
    }

    /// Разовый вопрос к Ollama (без стрима) — синтез ответа по сниппетам.
    static func ask(question: String, system: String, model: String,
                    ollama: String) async -> String {
        guard let url = URL(string: ollama + "/api/chat") else { return "" }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 180
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "model": model,
            "messages": [["role": "system", "content": system],
                         ["role": "user", "content": question]],
            "stream": false,
            "think": false,
            "keep_alive": "30m",
            "options": ["temperature": 0.3, "num_ctx": 8192, "num_predict": 1024],
        ] as [String: Any])
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]   // локальный вызов мимо системного прокси
        let session = URLSession(configuration: cfg)
        guard let (data, _) = try? await session.data(for: req),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let msg = obj["message"] as? [String: Any],
              let text = msg["content"] as? String else { return "" }
        return text
    }
}
