import Foundation

/// One outgoing graph hop over already-read documents, never another disk scan.
enum ArchiveLinks {
    struct Neighbor: Equatable {
        let rel: String
        let parent: String
    }

    static func neighbors(seeds: [String], documents: [String: String], limit: Int = 2) -> [Neighbor] {
        guard limit > 0 else { return [] }
        let pattern = #"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]"#
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
        let names = Dictionary(grouping: documents.keys, by: {
            ($0 as NSString).lastPathComponent
        })
        var seen = Set(seeds)
        var result: [Neighbor] = []
        for seed in seeds {
            let text = documents[seed] ?? ""
            for match in regex.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
                guard let range = Range(match.range(at: 1), in: text) else { continue }
                let target = String(text[range]).trimmingCharacters(in: .whitespaces)
                guard !target.hasPrefix("/"), !target.contains("\\"),
                      !target.split(separator: "/").contains(where: { $0.hasPrefix(".") }) else { continue }
                let file = target.hasSuffix(".md") ? target : target + ".md"
                let rel: String?
                if target.contains("/") { rel = documents[file] == nil ? nil : file }
                else { rel = names[file]?.count == 1 ? names[file]?.first : nil }
                guard let rel, seen.insert(rel).inserted else { continue }
                let parts = rel.split(separator: "/")
                guard let top = parts.first, GraphFolders.all.contains(String(top)),
                      !parts.contains(where: { $0.hasPrefix("_") || $0.hasPrefix(".") }) else { continue }
                result.append(Neighbor(rel: rel, parent: seed))
                if result.count >= limit { return result }
            }
        }
        return result
    }
}
