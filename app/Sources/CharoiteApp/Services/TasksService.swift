import Foundation

/// Задачи со встреч: строки `- [ ]` по всему графу (Obsidian-конвенция).
///
/// Минутки пишут поручения чекбоксами, заметки и ручные файлы — тоже;
/// сервис собирает их в один список и отмечает выполненное ПРЯМО в
/// markdown-файле (`- [ ]` → `- [x]`), так что Obsidian и приложение
/// всегда видят одно и то же. Никакой своей базы — файлы и есть истина.
@MainActor
final class TasksService: ObservableObject {
    static let shared = TasksService()

    struct Item: Identifiable, Equatable {
        let id: String          // путь#номер-строки — стабилен между сканами
        let file: URL
        let rel: String
        let lineIndex: Int
        let text: String        // без маркера чекбокса
        let done: Bool
        let fileDate: Date
    }

    @Published private(set) var items: [Item] = []
    @Published private(set) var openCount = 0

    // Литеральный паттерн — ошибка компиляции невозможна.
    // swiftlint:disable:next force_try
    private static let todoRx = try! NSRegularExpression(pattern: #"^\s*[-*] \[( |x|X)\] +(.+)$"#)

    /// Полный скан графа. Быстрый (только чтение .md), зовётся при открытии
    /// окна задач и после каждой отметки. root — для тестов (по умолчанию
    /// граф из настроек).
    func rescan(root: URL? = nil) {
        guard var graph = root ?? AppSettings.graphDir else { items = []; openCount = 0; return }
        graph = graph.resolvingSymlinksInPath()   // /var vs /private/var — см. ArchiveSearch
        var found: [Item] = []
        let keys: [URLResourceKey] = [.contentModificationDateKey]
        guard let walker = FileManager.default.enumerator(
            at: graph, includingPropertiesForKeys: keys,
            options: [.skipsHiddenFiles]) else { return }
        for case let url as URL in walker {
            guard url.pathExtension == "md",
                  let text = try? String(contentsOf: url, encoding: .utf8) else { continue }
            guard text.contains("- [") else { continue }
            let canon = url.resolvingSymlinksInPath().path
            let rel = canon.hasPrefix(graph.path + "/")
                ? String(canon.dropFirst(graph.path.count + 1))
                : url.lastPathComponent
            let mdate = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?
                .contentModificationDate ?? .distantPast
            for (i, line) in text.components(separatedBy: "\n").enumerated() {
                let range = NSRange(line.startIndex..., in: line)
                guard let m = Self.todoRx.firstMatch(in: line, range: range),
                      let markRange = Range(m.range(at: 1), in: line),
                      let textRange = Range(m.range(at: 2), in: line) else { continue }
                let done = line[markRange].lowercased() == "x"
                found.append(Item(
                    id: "\(rel)#\(i)", file: url, rel: rel, lineIndex: i,
                    text: String(line[textRange]), done: done, fileDate: mdate))
            }
        }
        // открытые сверху, внутри — свежие файлы первыми
        items = found.sorted {
            if $0.done != $1.done { return !$0.done }
            return $0.fileDate > $1.fileDate
        }
        openCount = items.filter { !$0.done }.count
    }

    /// Переключить чекбокс точечной заменой строки в файле.
    func toggle(_ item: Item, root: URL? = nil) {
        guard var text = try? String(contentsOf: item.file, encoding: .utf8) else { return }
        var lines = text.components(separatedBy: "\n")
        guard item.lineIndex < lines.count else { rescan(root: root); return }
        let line = lines[item.lineIndex]
        let flipped = item.done
            ? line.replacingOccurrences(of: "[x]", with: "[ ]")
                  .replacingOccurrences(of: "[X]", with: "[ ]")
            : line.replacingOccurrences(of: "[ ]", with: "[x]")
        guard flipped != line else { rescan(root: root); return }
        lines[item.lineIndex] = flipped
        text = lines.joined(separator: "\n")
        try? text.write(to: item.file, atomically: true, encoding: .utf8)
        rescan(root: root)
    }
}
