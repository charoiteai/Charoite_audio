import Foundation

#if os(macOS)

/// Поиск по встречам: найти прошлое решение по слову, не выходя из приложения.
///
/// Окно «Последние встречи» отвечает на «что случилось со вчерашней записью»,
/// но не на «где мы решали про ретеншн». Для второго раньше был только grep
/// или Obsidian. Здесь — лексический поиск по готовым документам встреч:
/// саммари, минуткам, разборам и заметкам графа. Стенограммы не сканируются
/// сознательно: они в десятки раз больше, а решения и поручения к моменту
/// поиска уже вынесены в верхние слои.
enum MeetingSearch {
    struct Hit: Identifiable, Equatable {
        let file: URL
        /// Человеческое имя места: тема встречи из имени папки или файла.
        let title: String
        /// Строка, где совпало, — обрезанная до читаемой.
        let snippet: String
        /// Дата из имени папки/файла — для сортировки новые первыми.
        let day: String

        var id: String { file.path + snippet }
    }

    /// Какие файлы встречи стоят прочтения при поиске.
    static let layers = ["Саммари.md", "Минутки.md", "Разбор.md"]

    static func search(_ query: String, graph: URL, limit: Int = 30) -> [Hit] {
        let tokens = tokens(of: query)
        guard !tokens.isEmpty else { return [] }
        var hits: [Hit] = []

        let archive = graph.appendingPathComponent("Встречи-архив")
        let folders = ((try? FileManager.default.contentsOfDirectory(
            at: archive, includingPropertiesForKeys: nil)) ?? [])
            .filter { !$0.lastPathComponent.hasPrefix("_") }
            .sorted { $0.lastPathComponent > $1.lastPathComponent }   // новые первыми

        for folder in folders {
            for layer in layers {
                let file = folder.appendingPathComponent(layer)
                guard let hit = match(file: file, tokens: tokens,
                                      title: folder.lastPathComponent) else { continue }
                hits.append(hit)
                break       // одной находки на встречу достаточно: ведём к папке
            }
            if hits.count >= limit { return hits }
        }

        // Заметки графа — на случай встреч, не доехавших до архива.
        let notes = graph.appendingPathComponent("Встречи")
        let noteFiles = ((try? FileManager.default.contentsOfDirectory(
            at: notes, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.pathExtension == "md" }
            .sorted { $0.lastPathComponent > $1.lastPathComponent }
        let seenDays = Set(hits.map(\.day))
        for note in noteFiles {
            guard hits.count < limit else { break }
            let day = dayKey(note.lastPathComponent)
            if seenDays.contains(day) { continue }   // архивная папка уже нашлась
            if let hit = match(file: note, tokens: tokens,
                               title: note.deletingPathExtension().lastPathComponent) {
                hits.append(hit)
            }
        }
        return hits
    }

    /// Все ли слова запроса есть в файле; сниппет — первая строка с совпадением.
    static func match(file: URL, tokens: [String], title: String) -> Hit? {
        guard let text = try? String(contentsOf: file, encoding: .utf8) else { return nil }
        let lower = text.lowercased()
        guard tokens.allSatisfy({ lower.contains($0) }) else { return nil }
        let line = text.split(separator: "\n")
            .first { candidate in
                let l = candidate.lowercased()
                return tokens.contains { l.contains($0) }
            }
            .map { snippet(String($0)) } ?? ""
        return Hit(file: file, title: title, snippet: line, day: dayKey(title))
    }

    /// Ключ встречи для дедупа архива и заметок графа.
    ///
    /// Папка называется «2026-08-01 10-00 — Тема», заметка — «2026-08-01_1000»:
    /// сырые префиксы этих имён не совпадают никогда, и дедуп на них молча не
    /// работал — встреча приходила в результатах дважды. Одни цифры совпадают
    /// в обоих форматах: 2026-08-01 10-00 и 2026-08-01_1000 → «202608011000».
    static func dayKey(_ title: String) -> String {
        String(title.filter(\.isNumber).prefix(12))
    }

    static func tokens(of query: String) -> [String] {
        query.lowercased()
            .split { !$0.isLetter && !$0.isNumber }
            .map(String.init)
            .filter { $0.count > 1 }
    }

    /// Строка для списка: без markdown-мусора и не длиннее взгляда.
    static func snippet(_ raw: String) -> String {
        var s = raw.replacingOccurrences(of: "**", with: "")
            .replacingOccurrences(of: "[[", with: "")
            .replacingOccurrences(of: "]]", with: "")
            .trimmingCharacters(in: CharacterSet(charactersIn: " -#>*"))
        if s.count > 140 {
            s = String(s.prefix(140)) + "…"
        }
        return s
    }
}
#endif
