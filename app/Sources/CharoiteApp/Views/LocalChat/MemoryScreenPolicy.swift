import Foundation

#if os(macOS)

/// Чистая политика экрана «Память» (макет MOBILE_2026-08, «macOS: доделки»,
/// экран 1): источники из блока поиска, их вид и подписи, строка
/// происхождения ответа. Тестируется без UI и без Ollama, как
/// `TasksScreenPolicy` и `LibraryScreenPolicy` у соседних экранов.
enum MemoryScreenPolicy {

    /// Источник, подмешанный в контекст ответа. Чип показывает, что
    /// ПОДМЕШАЛИ в модель, а не что модель «процитировала»: список берётся
    /// из блока реального поиска, не из текста ответа.
    struct Source: Equatable, Codable, Identifiable {
        enum Kind: String, Codable {
            case meeting
            case node
            case dossier
            case doc
        }

        let rel: String
        let kind: Kind

        var id: String { rel }

        /// Подпись чипа: встречи — датой и темой, узлы и досье — именем.
        var title: String {
            let stem = (rel as NSString).lastPathComponent
                .replacingOccurrences(of: ".md", with: "")
            switch kind {
            case .meeting:
                if let pretty = MemoryScreenPolicy.meetingLabel(stem: stem) { return pretty }
                return L.t("Встреча", "Meeting", "会议") + " · " + stem
            case .node:
                return L.t("Узел", "Node", "节点") + " · " + stem
            case .dossier:
                return L.t("Досье", "Dossier", "档案") + " · " + stem
            case .doc:
                return stem
            }
        }
    }

    /// Папка графа → вид источника. Верхние папки — контракт графа
    /// (см. .claude/rules и graph_updater): встречи, узлы пяти типов,
    /// ядра — тоже узлы сквозных тем, досье, остальное — документы.
    static func kind(of rel: String) -> Source.Kind {
        let top = rel.split(separator: "/").first.map(String.init) ?? ""
        switch top {
        case "Встречи": return .meeting
        case "Люди", "Системы", "Команды", "Блокеры", "Модели", "Ядра": return .node
        case "Досье", "Dossiers": return .dossier
        default: return .doc
        }
    }

    /// «2026-08-21_1202_Перенос_встречи» → «Встреча 21.08 · Перенос встречи».
    /// Не распарсилось — nil, чип покажет стем как есть.
    static func meetingLabel(stem: String) -> String? {
        let pattern = #"^(\d{4})-(\d{2})-(\d{2})_\d{4,6}(?:-\d+)?(?:_(.+))?$"#
        guard let re = try? NSRegularExpression(pattern: pattern),
              let m = re.firstMatch(in: stem, range: NSRange(stem.startIndex..., in: stem))
        else { return nil }
        func group(_ i: Int) -> String? {
            guard let r = Range(m.range(at: i), in: stem) else { return nil }
            return String(stem[r])
        }
        guard let month = group(2), let day = group(3) else { return nil }
        let head = L.t("Встреча", "Meeting", "会议") + " \(day).\(month)"
        guard let theme = group(4)?.replacingOccurrences(of: "_", with: " "),
              !theme.isEmpty else { return head }
        return head + " · " + theme
    }

    /// Источники из блока поиска. Ловит и локальный формат («• путь»), и
    /// brain-блоки: любой путь с известной верхней папкой графа до «.md».
    /// Порядок появления сохраняется — он же ранг поиска; дубли схлопнуты.
    static func sources(from block: String, limit: Int = 6) -> [Source] {
        guard !block.isEmpty else { return [] }
        let pattern = #"(?:Встречи|Люди|Системы|Команды|Блокеры|Модели|Ядра|Досье|Dossiers|Документация)/[^\n\]\)`]*?\.md"#
        guard let re = try? NSRegularExpression(pattern: pattern) else { return [] }
        var seen: Set<String> = []
        var out: [Source] = []
        for m in re.matches(in: block, range: NSRange(block.startIndex..., in: block)) {
            guard let r = Range(m.range, in: block) else { continue }
            let rel = String(block[r]).trimmingCharacters(in: .whitespaces)
            if seen.contains(rel) { continue }
            seen.insert(rel)
            out.append(Source(rel: rel, kind: kind(of: rel)))
            if out.count >= limit { break }
        }
        return out
    }

    /// Строка происхождения под ответом: «локально · модель · время · что в
    /// контексте». У каждого числа источник; нолей и обещаний не пишем.
    static func metaLine(model: String, seconds: Int, meetingsInContext: Int,
                         memoryOn: Bool, weakMatches: Bool) -> String {
        var parts = [L.t("локально", "local", "本地"), model,
                     L.t("\(seconds) с", "\(seconds) s", "\(seconds) 秒")]
        if !memoryOn {
            parts.append(L.t("память выключена", "memory off", "记忆已关闭"))
        } else if weakMatches {
            parts.append(L.t("граф: слабые совпадения", "graph: weak matches", "图谱：弱匹配"))
        } else if meetingsInContext > 0 {
            parts.append(LibraryScreenPolicy.meetings(meetingsInContext) +
                         L.t(" в контексте", " in context", " 在上下文中"))
        } else {
            parts.append(L.t("граф не в контексте", "graph not in context", "图谱不在上下文中"))
        }
        return parts.joined(separator: " · ")
    }

    static func nodesLabel(_ n: Int) -> String {
        LibraryScreenPolicy.plural(n, ru: ("узел", "узла", "узлов"), en: ("node", "nodes"), zh: "个节点")
    }

    static func dossiersLabel(_ n: Int) -> String {
        LibraryScreenPolicy.plural(n, ru: ("досье", "досье", "досье"), en: ("dossier", "dossiers"), zh: "份档案")
    }
}

#endif
