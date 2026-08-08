import Foundation

/// Разбор «Минуток» — подробного протокола встречи.
///
/// Карточка встречи показывала только выжимку из Саммари: суть, решения и
/// поручения по одной строке. Рядом всё это время лежал файл с темами,
/// полными формулировками решений и сроками — человек его не видел и шёл
/// в Obsidian руками.
///
/// Разметку пишет модель, и пишет по-разному: `- Темы:` с вложенными
/// дефисами, `**Темы:**` с нумерацией и звёздочками, `## Темы` по шаблону
/// retro_fill. Поэтому заголовок узнаём по названию, а не по конкретному
/// синтаксису, и любой список считаем содержимым текущего раздела.
struct MeetingMinutes: Equatable {
    struct Item: Equatable {
        let text: String
        /// Глубина вложенности пункта: подпункты рисуются отступом.
        let level: Int
    }

    var topics: [Item] = []
    var decisions: [Item] = []
    var tasks: [Item] = []
    var openQuestions: [Item] = []
    var risks: [Item] = []
    /// Протокол собран, пока встреча ещё шла: значит, он неполон.
    var isDraft = false

    var isEmpty: Bool {
        topics.isEmpty && decisions.isEmpty && tasks.isEmpty
            && openQuestions.isEmpty && risks.isEmpty
    }

    private enum Section {
        case topics, decisions, tasks, openQuestions, risks, ignored
    }

    /// Заголовки, которые модель ставит над разделами. «Участники» узнаём
    /// намеренно: раздел уходит в ignored, иначе список людей утёк бы в темы.
    /// Китайские написания — из промпта минуток в src/llm.py (`## 议题`,
    /// `## 决定`, `## 行动项`, `## 待解决问题`, `## 风险`, `参会人`). Без них
    /// китайские минутки разбирались в ноль: разделы в файле есть, а карточка
    /// пустая — ровно как было с английской встречей до этой правки.
    private static let headings: [(names: [String], section: Section)] = [
        (["темы", "о чём говорили", "о чем говорили", "topics", "议题", "讨论了什么"], .topics),
        (["решения", "решили", "decisions", "决定"], .decisions),
        (["поручения", "задачи", "action items", "行动项", "任务"], .tasks),
        (["открытые вопросы", "вопросы", "open questions", "待解决问题"], .openQuestions),
        (["риски", "risks", "风险"], .risks),
        (["участники", "дата/время", "participants", "参会人", "日期/时间"], .ignored),
    ]

    static func parse(_ text: String) -> MeetingMinutes {
        var out = MeetingMinutes()
        out.isDraft = text.contains("черновик")
        var current: Section?
        for raw in text.components(separatedBy: .newlines) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("<!--") || line.hasPrefix("---") { continue }
            if let (section, inlineTail) = heading(in: line) {
                current = section
                // «- Участники: Иванов, Петров» — содержимое в той же строке.
                if let tail = inlineTail, !tail.isEmpty {
                    out.append(Item(text: tail, level: 0), to: section)
                }
                continue
            }
            guard let section = current else { continue }
            let body = stripMarkers(line)
            guard !body.isEmpty, body.lowercased() != "нет" else { continue }
            out.append(Item(text: body, level: depth(of: raw)), to: section)
        }
        return out
    }

    private mutating func append(_ item: Item, to section: Section) {
        switch section {
        case .topics: topics.append(item)
        case .decisions: decisions.append(item)
        case .tasks: tasks.append(item)
        case .openQuestions: openQuestions.append(item)
        case .risks: risks.append(item)
        case .ignored: break
        }
    }

    /// Заголовок раздела и текст, оставшийся в той же строке после двоеточия.
    private static func heading(in line: String) -> (Section, String?)? {
        let bare = stripMarkers(line)
        // «## Темы» — заголовок шаблона retro_fill, двоеточия в нём нет.
        guard let colon = bare.firstIndex(of: ":") else {
            return section(named: bare, hasBody: false).map { ($0, nil) }
        }
        let name = String(bare[bare.startIndex..<colon])
        // Двоеточие внутри длинной фразы — это пункт, а не заголовок.
        guard name.count <= 24 else { return nil }
        let tail = bare[bare.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        return section(named: name, hasBody: !tail.isEmpty).map { ($0, tail) }
    }

    /// Заголовок по названию раздела.
    ///
    /// Модель охотно дописывает к заголовку хвост («Темы обсуждения»,
    /// «Решения и договорённости») и ставит перед ним эмодзи. Поэтому имя
    /// чистим от всего, кроме букв, и разрешаем начинаться с ключа — но
    /// только когда в строке больше ничего нет. Иначе пункт «Решения по
    /// бюджету: снизить на 5%» стал бы заголовком и съел следующие строки.
    private static func section(named raw: String, hasBody: Bool) -> Section? {
        let name = raw.lowercased()
            .filter { $0.isLetter || $0.isWhitespace || $0 == "/" }
            .split(whereSeparator: \.isWhitespace).joined(separator: " ")
        guard !name.isEmpty else { return nil }
        for entry in headings {
            for key in entry.names {
                if name == key { return entry.section }
                if !hasBody, name.hasPrefix(key + " ") { return entry.section }
            }
        }
        return nil
    }

    /// Отступ в пробелах → уровень вложенности. Табы считаем как четыре
    /// пробела: модель мешает их со звёздочками в одном файле.
    private static func depth(of raw: String) -> Int {
        var spaces = 0
        for ch in raw {
            if ch == " " { spaces += 1 } else if ch == "\t" { spaces += 4 } else { break }
        }
        return min(spaces / 2, 2)
    }

    /// Снять маркеры списка, чекбоксы и markdown-жир: в карточке они шум.
    private static func stripMarkers(_ line: String) -> String {
        var s = line.trimmingCharacters(in: .whitespaces)
        while let first = s.first, "-*#•".contains(first) {
            s = String(s.dropFirst()).trimmingCharacters(in: .whitespaces)
        }
        // «1.» / «12)» в начале пункта
        if let match = s.range(of: #"^\d+[.)]\s*"#, options: .regularExpression) {
            s = String(s[match.upperBound...])
        }
        for box in ["[ ]", "[x]", "[X]"] where s.hasPrefix(box) {
            s = String(s.dropFirst(box.count)).trimmingCharacters(in: .whitespaces)
        }
        return s.replacingOccurrences(of: "**", with: "")
            .trimmingCharacters(in: .whitespaces)
    }
}
