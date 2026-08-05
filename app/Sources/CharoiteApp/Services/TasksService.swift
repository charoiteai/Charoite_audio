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

    struct Item: Identifiable, Equatable, Sendable {
        let id: String          // путь#номер-строки — стабилен между сканами
        let file: URL
        let rel: String
        let lineIndex: Int
        let text: String        // без маркера чекбокса
        let done: Bool
        let fileDate: Date
        /// Полная строка в момент скана. При переключении проверяем её снова:
        /// внешний редактор мог вставить строку, и старый lineIndex уже укажет
        /// на соседнее поручение.
        let sourceLine: String

        /// Когда поручение прозвучало: дата встречи из имени папки, а не
        /// mtime файла. Ночная ревизия трогает старые архивы и переставляла
        /// их наверх списка — по времени файла «свежей» оказывалась встреча
        /// недельной давности.
        var happenedAt: Date { TasksService.meetingDate(rel) ?? fileDate }
    }

    enum ToggleResult: Equatable, Sendable {
        case changed
        case missing
        case conflict
        case writeFailed
    }

    @Published private(set) var items: [Item] = []
    @Published private(set) var openCount = 0
    @Published private(set) var mutationError: String?
    @Published private(set) var pendingIDs: Set<String> = []
    private var scanGeneration = 0

    // Литеральный паттерн — ошибка компиляции невозможна.
    // nonisolated: константа читается из фонового скана, а не только с
    // главного актора (на Swift 6 обращение к изолированному статику из
    // nonisolated-контекста — ошибка, а не предупреждение).
    // swiftlint:disable:next force_try
    private nonisolated static let todoRx = try! NSRegularExpression(pattern: #"^\s*[-*] \[( |x|X)\] +(.+)$"#)

    /// Полный скан графа — в фоне, с публикацией результата на главном потоке.
    ///
    /// Раньше он был синхронным на @MainActor и звался при каждом запуске
    /// приложения, при каждом открытии окна задач и после каждой отметки
    /// чекбокса. На графе в тысячи заметок это чтение всех .md подряд —
    /// секунды замороженного интерфейса в самых обычных сценариях, причём
    /// pull-to-refresh морозил его же спиннер.
    func rescan(root: URL? = nil) {
        let target = root ?? AppSettings.graphDir
        scanGeneration += 1
        let generation = scanGeneration
        Task.detached(priority: .utility) { [target] in
            let found = Self.scanSync(graph: target)
            await MainActor.run {
                guard generation == self.scanGeneration else { return }
                self.apply(found)
            }
        }
    }

    /// Синхронный скан: вызывается из фона, а в тестах — напрямую.
    nonisolated static func scanSync(graph root: URL?) -> [Item] {
        guard var graph = root else { return [] }
        graph = graph.resolvingSymlinksInPath()   // /var vs /private/var — см. ArchiveSearch
        var found: [Item] = []
        let keys: [URLResourceKey] = [.contentModificationDateKey]
        guard let walker = FileManager.default.enumerator(
            at: graph, includingPropertiesForKeys: keys,
            options: [.skipsHiddenFiles]) else { return [] }
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
                    text: String(line[textRange]), done: done, fileDate: mdate,
                    sourceLine: line))
            }
        }
        return preferMeetingMinutes(found)
    }

    /// Конвейер может вынести одно поручение и в заметку встречи, и в её
    /// Минутки.md. Оба файла нужны, но два одинаковых чекбокса в приложении —
    /// ложные две задачи. При точном совпадении встречи и текста минутки
    /// выигрывают; разные формулировки и обычные заметки не склеиваются.
    nonisolated static func preferMeetingMinutes(_ items: [Item]) -> [Item] {
        let minuteKeys = Set(items.compactMap { item -> String? in
            guard item.file.lastPathComponent == "Минутки.md",
                  let meeting = meetingKey(item.rel) else { return nil }
            return meeting + "\u{0}" + normalizedTaskText(item.text)
        })
        return items.filter { item in
            guard item.file.lastPathComponent != "Минутки.md",
                  let meeting = meetingKey(item.rel) else { return true }
            return !minuteKeys.contains(meeting + "\u{0}" + normalizedTaskText(item.text))
        }
    }

    nonisolated private static func normalizedTaskText(_ text: String) -> String {
        text.replacingOccurrences(of: "**", with: "")
            .lowercased()
            .split(whereSeparator: \.isWhitespace)
            .joined(separator: " ")
    }

    /// Публикация результата — единственное, что делается на главном потоке.
    private func apply(_ found: [Item]) {
        // открытые сверху, внутри — свежие встречи первыми
        items = found.sorted {
            if $0.done != $1.done { return !$0.done }
            if $0.happenedAt != $1.happenedAt { return $0.happenedAt > $1.happenedAt }
            // Один файл: сохраняем порядок строк, иначе пункты одной встречи
            // перемешиваются между сканами.
            return $0.lineIndex < $1.lineIndex
        }
        openCount = items.filter { !$0.done }.count
    }

    /// Поручения одной встречи лежат в её архивной папке и несут тот же
    /// цифровой ключ, что статус: `2026-08-04 11-31` и
    /// `2026-08-04_1131` → `202608041131`.
    nonisolated static func meetingKey(_ value: String) -> String? {
        let key = String(value.filter(\.isNumber).prefix(12))
        return key.count == 12 ? key : nil
    }

    /// Дата и время встречи из того же ключа: `202608041131` → 4 августа 11:31.
    /// Час и минуты нужны, чтобы утренняя и вечерняя встречи одного дня
    /// стояли в списке в том порядке, в каком проходили.
    nonisolated static func meetingDate(_ rel: String) -> Date? {
        guard let key = meetingKey(rel), let value = Int(key) else { return nil }
        var parts = DateComponents()
        parts.year = value / 100_000_000
        parts.month = value / 1_000_000 % 100
        parts.day = value / 10_000 % 100
        parts.hour = value / 100 % 100
        parts.minute = value % 100
        guard (1...12).contains(parts.month ?? 0), (1...31).contains(parts.day ?? 0),
              (0...23).contains(parts.hour ?? 24), (0...59).contains(parts.minute ?? 60) else {
            return nil
        }
        return Calendar.current.date(from: parts)
    }

    nonisolated static func belongs(_ item: Item, to meetingID: String) -> Bool {
        guard let source = meetingKey(item.rel), let meeting = meetingKey(meetingID) else {
            return false
        }
        return source == meeting
    }

    func items(for meetingID: String, includeDone: Bool = true) -> [Item] {
        Self.meetingItems(items, for: meetingID, includeDone: includeDone)
    }

    nonisolated static func meetingItems(
        _ items: [Item],
        for meetingID: String,
        includeDone: Bool = true
    ) -> [Item] {
        let matches = items.filter { belongs($0, to: meetingID) }
        // Одна встреча может продублировать поручение в заметке графа и в
        // архивных минутках. Для карточки канонический редактируемый список —
        // Минутки.md; к заметке откатываемся только у старых встреч без них.
        let minutes = matches.filter { $0.file.lastPathComponent == "Минутки.md" }
        let canonical = minutes.isEmpty ? matches : minutes
        return includeDone ? canonical : canonical.filter { !$0.done }
    }

    func isUpdating(_ item: Item) -> Bool {
        pendingIDs.contains(item.id)
    }

    /// Короткое имя источника для секции задач. Для архивной встречи вместо
    /// `Встречи-архив/2026-08-04 11-31 — План/Минутки.md` показываем тему.
    nonisolated static func sourceTitle(_ rel: String) -> String {
        let parts = rel.split(separator: "/").map(String.init)
        if let folder = parts.first(where: { meetingKey($0) != nil }),
           let divider = folder.range(of: " — ") {
            return String(folder[divider.upperBound...])
        }
        return URL(fileURLWithPath: rel).deletingPathExtension().lastPathComponent
    }

    /// Переключить чекбокс вне главного потока. Пока запись идёт, повторный
    /// клик по той же строке блокируется; после изменения перечитываем граф.
    func toggle(_ item: Item, root: URL? = nil) {
        guard !pendingIDs.contains(item.id) else { return }
        mutationError = nil
        pendingIDs.insert(item.id)
        let target = root ?? AppSettings.graphDir
        Task.detached(priority: .userInitiated) {
            let result = Self.toggleSync(item)
            await MainActor.run {
                self.pendingIDs.remove(item.id)
                switch result {
                case .changed:
                    self.rescan(root: target)
                case .missing:
                    self.mutationError = L.t(
                        "Поручение уже изменилось или было удалено. Список обновлён.",
                        "The action item changed or was deleted. The list was refreshed.",
                        "该任务已更改或删除。列表已刷新。")
                    self.rescan(root: target)
                case .conflict:
                    self.mutationError = L.t(
                        "Файл изменился в другом редакторе. Проверьте поручение и повторите.",
                        "The file changed in another editor. Check the action item and try again.",
                        "文件已在其他编辑器中更改。请检查任务后重试。")
                    self.rescan(root: target)
                case .writeFailed:
                    self.mutationError = L.t(
                        "Не удалось сохранить поручение. Markdown-файл не изменён.",
                        "The action item could not be saved. The Markdown file was not changed.",
                        "无法保存任务。Markdown 文件未更改。")
                }
            }
        }
    }

    /// Синхронное ядро для фоновой записи и регрессий.
    ///
    /// lineIndex — лишь быстрый путь. Если файл сдвинулся, ищем ровно одну
    /// строку с тем же текстом и состоянием. Два одинаковых кандидата —
    /// конфликт: лучше не отметить ничего, чем закрыть чужое поручение.
    nonisolated static func toggleSync(_ item: Item) -> ToggleResult {
        guard var text = try? String(contentsOf: item.file, encoding: .utf8) else {
            return .missing
        }
        var lines = text.components(separatedBy: "\n")
        let targetIndex: Int
        if item.lineIndex < lines.count, lines[item.lineIndex] == item.sourceLine {
            targetIndex = item.lineIndex
        } else {
            let candidates = lines.indices.filter { index in
                guard let parsed = parse(lines[index]) else { return false }
                return parsed.text == item.text && parsed.done == item.done
            }
            guard !candidates.isEmpty else { return .missing }
            guard candidates.count == 1, let only = candidates.first else { return .conflict }
            targetIndex = only
        }
        let line = lines[targetIndex]
        let flipped = item.done
            ? line.replacingOccurrences(of: "[x]", with: "[ ]")
                  .replacingOccurrences(of: "[X]", with: "[ ]")
            : line.replacingOccurrences(of: "[ ]", with: "[x]")
        guard flipped != line else { return .conflict }
        lines[targetIndex] = flipped
        text = lines.joined(separator: "\n")
        do {
            try text.write(to: item.file, atomically: true, encoding: .utf8)
            return .changed
        } catch {
            return .writeFailed
        }
    }

    nonisolated private static func parse(_ line: String) -> (done: Bool, text: String)? {
        let range = NSRange(line.startIndex..., in: line)
        guard let match = todoRx.firstMatch(in: line, range: range),
              let markRange = Range(match.range(at: 1), in: line),
              let textRange = Range(match.range(at: 2), in: line) else { return nil }
        return (line[markRange].lowercased() == "x", String(line[textRange]))
    }
}
