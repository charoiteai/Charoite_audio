import Combine
import Foundation

#if os(macOS)

/// «Что знает память» — инвентарь графа для правой колонки экрана Память
/// (макет MOBILE_2026-08): счёт встреч/узлов/досье и свежие ядра со строкой
/// состояния. Скан — в фоне, кэш по времени: колонка не смеет трогать диск
/// на каждую перерисовку (урок кэша глубин из #391 наоборот: здесь данные
/// меняются раз в встречу, а не под ногами).
@MainActor
final class GraphInventoryService: ObservableObject {
    static let shared = GraphInventoryService()

    struct Core: Identifiable, Equatable, Sendable {
        let name: String
        let folder: String   // папка контракта («Ядра»/«Cores»/«核心») — для клика
        let status: String   // первая содержательная строка ядра; пусто — нет
        let updated: Date
        var id: String { folder + "/" + name }
    }

    struct Snapshot: Equatable, Sendable {
        var meetings = 0
        var nodes = 0
        var dossiers = 0
        var cores: [Core] = []
    }

    @Published private(set) var snapshot = Snapshot()
    /// nil — граф не настроен: колонка обязана сказать это словами, а не
    /// рисовать «0 встреч» (круг-1, GLM: нули читаются как «память пуста»).
    @Published private(set) var configured = AppSettings.graphDir != nil
    private var scannedAt: Date?
    private var scanning = false

    /// Освежить не чаще раза в минуту: appear/переключения дешёвые.
    func refresh() {
        configured = AppSettings.graphDir != nil
        if let at = scannedAt, Date().timeIntervalSince(at) < 60 { return }
        guard !scanning, let graph = AppSettings.graphDir else { return }
        scanning = true
        scannedAt = Date()   // метка на старте: тик пульса не холостит через раз
        Task.detached(priority: .utility) {
            let snap = Self.scan(graph: graph)
            await MainActor.run {
                let service = GraphInventoryService.shared
                service.snapshot = snap
                service.scanning = false
            }
        }
    }

    nonisolated private static func mdCount(_ dir: URL) -> Int {
        (try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil))?
            .filter { $0.pathExtension == "md" && !$0.lastPathComponent.hasPrefix(".")
                && !GraphFolders.isServiceFile($0.lastPathComponent) }
            .count ?? 0
    }

    /// Встречи графа, а не файлы: заметка лежит в «Встречи/» файлом, архив той
    /// же встречи — папкой «Встречи-архив/<дата — тема>/» с минутками внутри.
    /// Плоский счёт .md не видел архивных папок вовсе (граф владельца 21.10:
    /// 392 заметки, 409 архивных папок, встреч по ключу — 411; №757, круг in1).
    /// Одна встреча — один ключ конвейера «ГГГГ-ММ-ДД_ЧЧММ[СС][-N]»
    /// (`meeting_stamp.graph_key`): секунды получает вторая встреча той же
    /// минуты, «-N» — встреча-коллизия той же секунды, обе отдельные (круги
    /// out1–out2). Ключ берётся существующими правилами, а не своим разбором:
    /// заметка — `MemoryScreenPolicy.stamp(of:)`, архивная папка — `meeting_id`
    /// манифеста (как `meeting_archive._folders_for`), имя папки
    /// «ГГГГ-ММ-ДД ЧЧ-ММ[-СС][-N] — тема» (`archive_time`) — только без
    /// манифеста. Цифры темы («Бюджет 2027») в ключ не попадают. Запись без
    /// ключа — одна встреча.
    nonisolated static func meetingIdentity(_ name: String) -> String? {
        if let key = MemoryScreenPolicy.stamp(of: name) { return key }
        let ns = name as NSString
        guard let m = archiveFolderRx.firstMatch(in: name, range: NSRange(location: 0, length: ns.length)) else {
            return nil
        }
        let parts = (1..<m.numberOfRanges).map { i -> String in
            let r = m.range(at: i)
            return r.location == NSNotFound ? "" : ns.substring(with: r)
        }
        return MemoryScreenPolicy.stamp(of: "\(parts[0])_\(parts[1])\(parts[2])\(parts[3])\(parts[4])")
    }

    nonisolated static func meetingIdentity(folder: URL) -> String? {
        if let data = try? Data(contentsOf: folder.appendingPathComponent("meeting.meta.json")),
           let meta = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let id = meta["meeting_id"] as? String,
           let key = MemoryScreenPolicy.stamp(of: id) {
            return key
        }
        return meetingIdentity(folder.lastPathComponent)
    }

    // swiftlint:disable:next force_try
    nonisolated private static let archiveFolderRx = try! NSRegularExpression(
        pattern: #"^(\d{4}-\d{2}-\d{2}) (\d{2})-(\d{2})(?:-(\d{2}))?(-\d+)?(?![\d-])"#)

    nonisolated static func meetingCount(graph: URL) -> Int {
        var keys = Set<String>()
        var keyless = 0
        for folder in GraphFolders.meetings {
            let entries = (try? FileManager.default.contentsOfDirectory(
                at: graph.appendingPathComponent(folder),
                includingPropertiesForKeys: [.isDirectoryKey])) ?? []
            for url in entries {
                let name = url.lastPathComponent
                guard !name.hasPrefix("."), !GraphFolders.isServiceFile(name) else { continue }
                let isDir = (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true
                guard isDir || url.pathExtension == "md" else { continue }
                let key = isDir ? meetingIdentity(folder: url) : meetingIdentity(name)
                if let key { keys.insert(key) } else { keyless += 1 }
            }
        }
        return keys.count + keyless
    }

    /// Папки ядер по контракту — свежие «сквозные темы» для нижнего блока.
    nonisolated private static let coreFolders = GraphFolders.cores

    nonisolated static func scan(graph: URL, coreLimit: Int = 4) -> Snapshot {
        // Имена папок — ЕДИНЫЙ контракт с политикой источников (круг-1,
        // Codex+GLM: две частные таблицы разошлись, en/zh-граф давал нули).
        typealias GC = GraphFolders
        var snap = Snapshot()
        snap.meetings = meetingCount(graph: graph)
        let plainNodes = GC.nodes.filter { !coreFolders.contains($0) }
        snap.nodes = plainNodes.map { mdCount(graph.appendingPathComponent($0)) }.reduce(0, +)
        snap.dossiers = GC.dossiers.map { mdCount(graph.appendingPathComponent($0)) }.reduce(0, +)
        // Ядра — узлы сквозных тем; в счёт узлов входят тоже.
        let coreFiles = coreFolders.flatMap { folder -> [(String, URL)] in
            ((try? FileManager.default.contentsOfDirectory(
                at: graph.appendingPathComponent(folder),
                includingPropertiesForKeys: [.contentModificationDateKey]))?
                // указатель «_ЯДРА.md» пересобирается каждую ночь — свежайший
                // mtime сажал его в блок ядер как тему (№750, круг in1)
                .filter { $0.pathExtension == "md" && !$0.lastPathComponent.hasPrefix(".")
                    && !GraphFolders.isServiceFile($0.lastPathComponent) } ?? [])
                .map { (folder, $0) }
        }
        snap.nodes += coreFiles.count
        let dated: [(String, URL, Date)] = coreFiles.map { folder, url in
            (folder, url, (try? url.resourceValues(forKeys: [.contentModificationDateKey])
                .contentModificationDate) ?? .distantPast)
        }
        snap.cores = dated.sorted { $0.2 > $1.2 }.prefix(coreLimit).map { folder, url, date in
            Core(name: url.deletingPathExtension().lastPathComponent, folder: folder,
                 status: coreStatus(of: url), updated: date)
        }
        return snap
    }

    /// Строка состояния ядра: первая содержательная строка после заголовка —
    /// без разметки, обрезана до фразы. Файл читаем куском, не целиком.
    nonisolated static func coreStatus(of url: URL) -> String {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return "" }
        defer { try? handle.close() }
        // 16 КБ и потерпимое декодирование: 4 КБ могли кончиться посреди
        // UTF-8-символа, и String(data:encoding:) молча отдавал nil
        // (круг-1, Codex); фронтматтер открывается только ПЕРВОЙ строкой
        // файла — «---» в теле это горизонтальная линейка, а не начало
        // фронтматтера (круг-1, GLM: статус ядра пустел).
        let data = (try? handle.read(upToCount: 16384)) ?? Data()
        let head = String(decoding: data, as: UTF8.self)
        var inFrontmatter = false
        for (i, raw) in head.components(separatedBy: "\n").enumerated() {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line == "---" {
                if i == 0 { inFrontmatter = true } else if inFrontmatter { inFrontmatter = false }
                continue
            }
            if inFrontmatter || line.isEmpty || line.hasPrefix("#") { continue }
            let clean = line
                .replacingOccurrences(of: "**", with: "")
                .replacingOccurrences(of: "[[", with: "")
                .replacingOccurrences(of: "]]", with: "")
                .trimmingCharacters(in: CharacterSet(charactersIn: "-•⚠️🔴🟢📌 "))
            if clean.isEmpty { continue }
            return String(clean.prefix(60))
        }
        return ""
    }
}

#endif
