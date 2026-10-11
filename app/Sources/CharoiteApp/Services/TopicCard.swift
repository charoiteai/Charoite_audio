import Foundation

/// Read-only projection of an existing dossier/core. No inference or graph writes.
enum TopicCard {
    enum Section: String, CaseIterable, Sendable {
        case current, changes, decisions, open, uncertainty, people, author
    }

    struct Source: Identifiable, Sendable {
        let target: String
        let url: URL?
        var id: String { target }
    }

    struct Snapshot: Sendable {
        let document: URL
        let isDossier: Bool
        let assembled: String?
        let modified: Date?
        let sections: [Section: String]
        let sources: [Section: [Source]]
    }

    enum ReadError: Error { case outsideGraph, unreadable }

    private static let headings: [String: Section] = [
        "Сейчас": .current, "Статус": .current, "Status": .current, "当前状态": .current, "状态": .current,
        "Как пришли": .changes, "История": .changes, "History": .changes, "历史": .changes,
        "Решено": .decisions, "Decisions": .decisions, "已决定": .decisions,
        "Открыто": .open, "Следующий шаг": .open, "Open": .open, "Next step": .open, "下一步": .open,
        "Неясности": .uncertainty, "Uncertainties": .uncertainty, "不确定事项": .uncertainty,
        "Кто в теме": .people, "People": .people,
        "Правки автора": .author, "Author edits": .author,
    ]

    /// Unknown level-two headings end the preceding section; code fences are literal.
    static func parse(_ text: String) -> (sections: [Section: String], assembled: String?) {
        let lines = text.replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n").components(separatedBy: "\n")
        var sections: [Section: String] = [:]
        var active: Section?
        var frontmatter = lines.first == "---"
        var assembled: String?
        var fence: String?
        for (index, raw) in lines.enumerated() {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if frontmatter {
                if index > 0 && line == "---" { frontmatter = false }
                if line.hasPrefix("собрано:") {
                    assembled = String(line.dropFirst("собрано:".count)).trimmingCharacters(in: .whitespaces)
                }
                continue
            }
            if line.hasPrefix("```") || line.hasPrefix("~~~") {
                let marker = String(line.prefix(3))
                if fence == nil { fence = marker } else if fence == marker { fence = nil }
            } else if fence == nil && line.hasPrefix("## ") {
                active = headings[String(line.dropFirst(3)).trimmingCharacters(in: .whitespaces)]
                continue
            }
            if let active { sections[active, default: ""] += raw + "\n" }
        }
        sections = sections.mapValues { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        return (sections, assembled)
    }

    static func linkTargets(_ text: String) -> [String] {
        let pattern = #"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]"#
        guard let regex = try? NSRegularExpression(pattern: pattern) else { return [] }
        var seen: Set<String> = []
        return regex.matches(in: text, range: NSRange(text.startIndex..., in: text)).compactMap { match in
            guard let range = Range(match.range(at: 1), in: text) else { return nil }
            let target = String(text[range]).trimmingCharacters(in: .whitespaces)
            return !target.isEmpty && seen.insert(target).inserted ? target : nil
        }
    }

    /// Ambiguous bare names, traversal, hidden files and symlinks outside the graph never open.
    static func resolve(_ target: String, graph: URL, files: [URL]) -> URL? {
        let parts = target.split(separator: "/", omittingEmptySubsequences: false)
        guard !target.hasPrefix("/"), !parts.contains(where: { $0.isEmpty || $0.hasPrefix(".") }),
              !target.contains("\\"), !target.contains(":") else { return nil }
        let name = target.hasSuffix(".md") ? target : target + ".md"
        let matches = files.filter { url in
            guard isInside(url, graph: graph) else { return false }
            let rel = String(url.resolvingSymlinksInPath().path
                .dropFirst(graph.resolvingSymlinksInPath().path.count + 1))
            guard !rel.split(separator: "/").contains(where: { $0.hasPrefix(".") }) else { return false }
            return target.contains("/") ? rel == name : url.lastPathComponent == name
        }
        return matches.count == 1 ? matches[0] : nil
    }

    static func isInside(_ url: URL, graph: URL) -> Bool {
        url.resolvingSymlinksInPath().path.hasPrefix(graph.resolvingSymlinksInPath().path + "/")
    }

    static func load(graph: URL, folder: String, name: String) throws -> Snapshot {
        let root = graph.resolvingSymlinksInPath()
        guard GraphFolders.cores.contains(folder), !name.isEmpty, !name.contains("/"),
              !name.contains("\\"), !name.hasPrefix(".") else { throw ReadError.outsideGraph }
        let core = root.appendingPathComponent(folder).appendingPathComponent(name + ".md")
        guard isInside(core, graph: root) else { throw ReadError.outsideGraph }
        // Duplicate dossier names are ambiguous: fall back to the core, never pick arbitrarily.
        let dossiers = GraphFolders.dossiers.map {
            root.appendingPathComponent($0).appendingPathComponent(name + ".md")
        }.filter { isInside($0, graph: root) && FileManager.default.fileExists(atPath: $0.path) }
        let document = dossiers.count == 1 ? dossiers[0] : core
        // Strict UTF-8: a broken sync fragment must not become a factual card.
        guard let text = try? String(contentsOf: document, encoding: .utf8) else { throw ReadError.unreadable }
        let parsed = parse(text)
        var files: [URL] = []
        for folder in GraphFolders.all {
            let dir = root.appendingPathComponent(folder)
            guard isInside(dir, graph: root), let walk = FileManager.default.enumerator(
                at: dir, includingPropertiesForKeys: [.isRegularFileKey], options: [.skipsHiddenFiles]
            ) else { continue }
            for case let url as URL in walk where url.pathExtension == "md" {
                if isInside(url, graph: root),
                   (try? url.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true {
                    files.append(url)
                }
            }
        }
        let sources = parsed.sections.mapValues { body in
            linkTargets(body).map { Source(target: $0, url: resolve($0, graph: root, files: files)) }
        }
        return Snapshot(document: document, isDossier: dossiers.count == 1, assembled: parsed.assembled,
                        modified: try? document.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate,
                        sections: parsed.sections, sources: sources)
    }
}
