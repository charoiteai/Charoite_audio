import Foundation
import SwiftUI

/// Чтение графа встреч прямо с телефона.
///
/// Пользователь один раз выбирает корень графа (папку Obsidian-vault
/// своего проекта) в Файлах — дальше лента встреч и задачи читаются
/// из тех же markdown-файлов, что видят Mac и Obsidian. Своей базы нет:
/// файлы и есть истина (та же конвенция, что в macOS TasksService).
///
/// iCloud-нюанс: файл может быть «облачным» (не скачан на телефон).
/// Такие не читаем синхронно — просим систему скачать и пропускаем;
/// следующий откр экрана их подхватит.
@MainActor
final class GraphStore: ObservableObject {
    static let shared = GraphStore()
    private static let bookmarkKey = "graph.bookmark"

    struct Meeting: Identifiable, Equatable {
        let id: String        // относительный путь
        let url: URL
        let title: String
        let stamp: String     // «27.07 15:34» для строки списка
        let sortKey: String   // имя файла YYYY-MM-DD_HHMM — сортировка убыв.
    }

    struct TaskItem: Identifiable, Equatable {
        let id: String        // путь#строка — стабилен между сканами
        let url: URL
        let rel: String
        let lineIndex: Int
        let text: String
        let done: Bool
    }

    @Published private(set) var meetings: [Meeting] = []
    @Published private(set) var tasks: [TaskItem] = []
    @Published private(set) var openCount = 0
    @Published var status: String?

    var folderChosen: Bool {
        UserDefaults.standard.data(forKey: Self.bookmarkKey) != nil
    }

    func saveFolder(_ url: URL) throws {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let bm = try url.bookmarkData()
        UserDefaults.standard.set(bm, forKey: Self.bookmarkKey)
    }

    private func graphRoot() -> URL? {
        guard let bm = UserDefaults.standard.data(forKey: Self.bookmarkKey) else { return nil }
        var stale = false
        guard let url = try? URL(resolvingBookmarkData: bm, bookmarkDataIsStale: &stale) else { return nil }
        if stale, let fresh = try? url.bookmarkData() {
            UserDefaults.standard.set(fresh, forKey: Self.bookmarkKey)
        }
        return url
    }

    // Тот же паттерн, что macOS TasksService.todoRx — форматы совпадают.
    // Литеральный паттерн — ошибка компиляции невозможна.
    // swiftlint:disable:next force_try
    private static let todoRx = try! NSRegularExpression(pattern: #"^\s*[-*] \[( |x|X)\] +(.+)$"#)

    /// Облачный файл без локальной копии: читать нельзя (повиснем на
    /// скачивании) — дёргаем загрузку и честно пропускаем.
    private func localizedText(of url: URL) -> String? {
        let vals = try? url.resourceValues(forKeys: [.ubiquitousItemDownloadingStatusKey])
        if let st = vals?.ubiquitousItemDownloadingStatus, st != .current {
            try? FileManager.default.startDownloadingUbiquitousItem(at: url)
            return nil
        }
        return try? String(contentsOf: url, encoding: .utf8)
    }

    /// Лента: Встречи/*.md, свежие сверху. Тема — из первого `# …` («… — Тема»).
    func rescanMeetings() {
        guard let root = graphRoot() else { meetings = []; return }
        let scoped = root.startAccessingSecurityScopedResource()
        defer { if scoped { root.stopAccessingSecurityScopedResource() } }
        let dir = root.appendingPathComponent("Встречи", isDirectory: true)
        let files = (try? FileManager.default.contentsOfDirectory(
            at: dir, includingPropertiesForKeys: [.ubiquitousItemDownloadingStatusKey],
            options: [.skipsHiddenFiles]))?.filter { $0.pathExtension == "md" } ?? []
        var out: [Meeting] = []
        var pending = 0
        for f in files {
            let name = f.deletingPathExtension().lastPathComponent  // 2026-07-27_1534
            guard let text = localizedText(of: f) else { pending += 1; continue }
            out.append(Meeting(
                id: "Встречи/\(f.lastPathComponent)", url: f,
                title: Self.title(from: text, fallback: name),
                stamp: Self.stamp(from: name), sortKey: name))
        }
        meetings = out.sorted { $0.sortKey > $1.sortKey }
        status = pending > 0 ? "Ещё скачивается из iCloud: \(pending)" : nil
    }

    /// Задачи `- [ ]` по всему графу — как на Mac, но щадяще к телефону:
    /// только файлы с чекбоксами, облачные без копии — в следующий раз.
    func rescanTasks() {
        guard let root = graphRoot() else { tasks = []; openCount = 0; return }
        let scoped = root.startAccessingSecurityScopedResource()
        defer { if scoped { root.stopAccessingSecurityScopedResource() } }
        var found: [TaskItem] = []
        guard let walker = FileManager.default.enumerator(
            at: root, includingPropertiesForKeys: [.ubiquitousItemDownloadingStatusKey],
            options: [.skipsHiddenFiles]) else { return }
        for case let url as URL in walker {
            guard url.pathExtension == "md", let text = localizedText(of: url),
                  text.contains("- [") else { continue }
            let rel = url.path.hasPrefix(root.path + "/")
                ? String(url.path.dropFirst(root.path.count + 1)) : url.lastPathComponent
            for (i, line) in text.components(separatedBy: "\n").enumerated() {
                let range = NSRange(line.startIndex..., in: line)
                guard let m = Self.todoRx.firstMatch(in: line, range: range),
                      let markRange = Range(m.range(at: 1), in: line),
                      let textRange = Range(m.range(at: 2), in: line) else { continue }
                found.append(TaskItem(
                    id: "\(rel)#\(i)", url: url, rel: rel, lineIndex: i,
                    text: String(line[textRange]),
                    done: line[markRange].lowercased() == "x"))
            }
        }
        // открытые сверху — как на Mac
        tasks = found.sorted { !$0.done && $1.done }
        openCount = found.filter { !$0.done }.count
    }

    /// Отметка — точечная замена маркера, файл остаётся истиной для всех.
    ///
    /// Две вещи здесь принципиальны. Первая: строку ищем по ТЕКСТУ задачи, а
    /// номер строки — лишь подсказка. Файл общий с Obsidian и Mac; если между
    /// сканом и тапом граф дописали сверху, индексы уехали, и галка вставала
    /// на соседнюю задачу — молча и не ту. Вторая: пишем через
    /// NSFileCoordinator, перечитывая файл внутри координации, иначе чужие
    /// правки затираются, а iCloud плодит конфликтные копии.
    func toggle(_ item: TaskItem) {
        guard let root = graphRoot() else { return }
        let scoped = root.startAccessingSecurityScopedResource()
        defer { if scoped { root.stopAccessingSecurityScopedResource() } }

        var coordError: NSError?
        var failure: String?
        NSFileCoordinator(filePresenter: nil).coordinate(
            writingItemAt: item.url, options: .forMerging, error: &coordError
        ) { url in
            guard let text = try? String(contentsOf: url, encoding: .utf8) else {
                failure = L.t("Файл ещё скачивается из iCloud",
                              "File is still downloading from iCloud",
                              "文件仍在从 iCloud 下载")
                return
            }
            var lines = text.components(separatedBy: "\n")
            let hinted = lines.indices.contains(item.lineIndex)
                && lines[item.lineIndex].contains(item.text)
            guard let idx = hinted ? item.lineIndex
                    : lines.firstIndex(where: { $0.contains(item.text) && Self.isTodo($0) }) else {
                failure = L.t("Задача изменилась в графе — список обновлён",
                              "Task changed in the graph — list refreshed",
                              "任务在图谱中已变更 — 列表已刷新")
                return
            }
            let line = lines[idx]
            let range = NSRange(line.startIndex..., in: line)
            guard let m = Self.todoRx.firstMatch(in: line, range: range),
                  let markRange = Range(m.range(at: 1), in: line) else { return }
            // Меняем ровно один символ маркера, не трогая остальную строку.
            var updated = line
            updated.replaceSubrange(markRange, with: item.done ? " " : "x")
            lines[idx] = updated
            do {
                // atomically: false — внутри координации так сохраняется
                // identity файла, и iCloud не считает это заменой документа.
                try lines.joined(separator: "\n").write(to: url, atomically: false, encoding: .utf8)
            } catch {
                failure = error.localizedDescription
            }
        }
        if let e = coordError ?? (failure.map { NSError(domain: "graph", code: 0,
                                                       userInfo: [NSLocalizedDescriptionKey: $0]) }) {
            status = L.t("Не удалось отметить: \(e.localizedDescription)",
                         "Could not update the task: \(e.localizedDescription)",
                         "无法更新任务：\(e.localizedDescription)")
        }
        rescanTasks()
    }

    private static func isTodo(_ line: String) -> Bool {
        todoRx.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)) != nil
    }

    /// Полный текст встречи для просмотра.
    func text(of meeting: Meeting) -> String {
        guard let root = graphRoot() else { return "" }
        let scoped = root.startAccessingSecurityScopedResource()
        defer { if scoped { root.stopAccessingSecurityScopedResource() } }
        return localizedText(of: meeting.url) ?? "Скачивается из iCloud…"
    }

    nonisolated static func title(from text: String, fallback: String) -> String {
        for line in text.components(separatedBy: "\n") where line.hasPrefix("# ") {
            let h = String(line.dropFirst(2))
            if let r = h.range(of: " — ") { return String(h[r.upperBound...]) }
            return h
        }
        return fallback
    }

    /// «2026-07-27_1534» → «27.07 15:34»
    nonisolated static func stamp(from name: String) -> String {
        let parts = name.split(separator: "_")
        guard parts.count >= 2, parts[0].count == 10, parts[1].count >= 4 else { return name }
        let d = parts[0].split(separator: "-")   // [2026, 07, 27]
        let t = parts[1]
        guard d.count == 3 else { return name }
        return "\(d[2]).\(d[1]) \(t.prefix(2)):\(t.dropFirst(2).prefix(2))"
    }
}
