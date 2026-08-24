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

    /// Контракт верхних папок графа — ЕДИНАЯ таблица для чипов источников и
    /// инвентаря правой колонки (круг-1 по PR #396: Codex и GLM независимо
    /// поймали, что политика знала только русские имена — на en/zh-графе
    /// чипов встреч не было вовсе, а две частные таблицы уже разошлись).
    /// Имена — ровно те, что пишет graph_updater трёх языков (demo/graph_en,
    /// demo/graph_zh) плюс архив встреч.
    enum GraphContract {
        static let meetings = ["Встречи", "Встречи-архив", "Meetings", "Meetings-archive", "会议", "会议归档"]
        static let nodes = ["Люди", "Системы", "Команды", "Блокеры", "Модели", "Ядра",
                            "People", "Systems", "Teams", "Blockers", "Models", "Cores",
                            "人物", "系统", "团队", "阻碍", "模型", "核心"]
        static let dossiers = ["Досье", "Dossiers", "档案"]
        static let docs = ["Документация", "Docs", "文档"]
        static var all: [String] { meetings + nodes + dossiers + docs }
    }

    /// Папка графа → вид источника. Ядра — тоже узлы сквозных тем;
    /// всё вне контракта — документы.
    static func kind(of rel: String) -> Source.Kind {
        let top = rel.split(separator: "/").first.map(String.init) ?? ""
        if GraphContract.meetings.contains(top) { return .meeting }
        if GraphContract.nodes.contains(top) { return .node }
        if GraphContract.dossiers.contains(top) { return .dossier }
        return .doc
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
        guard !block.isEmpty, limit > 0 else { return [] }
        // Скобки в имени файла легальны («Отчёт (черновик).md») — исключаем
        // только перевод строки, «]» ([[ссылки]]) и бэктик (круг-1, DS и GLM:
        // путь со скобкой молча терял чип и портил счёт контекста).
        let folders = GraphContract.all.joined(separator: "|")
        let pattern = "(?:" + folders + #")/[^\n\]`]*?\.md"#
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

    /// Каноничный штамп встречи из стема имени файла: «2026-08-21_1202_Тема»
    /// → «2026-08-21_1202», «2026-08-21_120245-2» → «2026-08-21_120245».
    static func stamp(of stem: String) -> String? {
        let pattern = #"^(\d{4}-\d{2}-\d{2}_\d{4,6})"#
        guard let re = try? NSRegularExpression(pattern: pattern),
              let m = re.firstMatch(in: stem, range: NSRange(stem.startIndex..., in: stem)),
              let r = Range(m.range(at: 1), in: stem) else { return nil }
        return String(stem[r])
    }

    /// Запись библиотеки для чипа встречи. Голый префикс путал пары «одной
    /// минуты» (#388: минутный штамп владельцу, секундный соседке — круг-1,
    /// DS/GLM/Codex сошлись): сначала точный штамп, затем префикс строго на
    /// границе сегмента, из кандидатов — самый специфичный (длиннейший).
    static func matchRecord(stem: String, ids: [String]) -> String? {
        let want = stamp(of: stem) ?? stem
        guard !want.isEmpty else { return nil }
        if let exact = ids.first(where: { $0 == want || stamp(of: $0) == want }) { return exact }
        func onBoundary(_ shorter: String, _ longer: String) -> Bool {
            guard longer.hasPrefix(shorter), longer.count > shorter.count else { return false }
            let next = longer[longer.index(longer.startIndex, offsetBy: shorter.count)]
            return next == "_" || next == "-" || next.isNumber
        }
        return ids.filter { onBoundary(want, $0) || onBoundary($0, want) }
            .max { $0.count < $1.count }
    }

    static func sourcesLabel(_ n: Int) -> String {
        LibraryScreenPolicy.plural(n, ru: ("источник", "источника", "источников"),
                                   en: ("source", "sources"), zh: "个来源")
    }

    /// Строка происхождения под ответом: «локально · модель · время · что в
    /// контексте». У каждого числа источник; нолей и обещаний не пишем.
    /// `sourcesInContext` — все подмешанные хиты (не только встречи): «граф
    /// не в контексте» при видимых чипах узлов — противоречие в одном
    /// подвале (круг-1, DS Critical + GLM).
    static func metaLine(model: String, seconds: Int, meetingsInContext: Int,
                         sourcesInContext: Int = 0,
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
        } else if sourcesInContext > 0 {
            parts.append(sourcesLabel(sourcesInContext) +
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
