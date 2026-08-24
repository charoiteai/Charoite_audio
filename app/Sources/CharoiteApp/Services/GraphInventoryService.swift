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
                && !$0.lastPathComponent.hasPrefix("_") }
            .count ?? 0
    }

    /// Папки ядер по контракту — свежие «сквозные темы» для нижнего блока.
    nonisolated private static let coreFolders = GraphFolders.cores

    nonisolated static func scan(graph: URL, coreLimit: Int = 4) -> Snapshot {
        // Имена папок — ЕДИНЫЙ контракт с политикой источников (круг-1,
        // Codex+GLM: две частные таблицы разошлись, en/zh-граф давал нули).
        typealias GC = GraphFolders
        var snap = Snapshot()
        snap.meetings = GC.meetings.map { mdCount(graph.appendingPathComponent($0)) }.reduce(0, +)
        let plainNodes = GC.nodes.filter { !coreFolders.contains($0) }
        snap.nodes = plainNodes.map { mdCount(graph.appendingPathComponent($0)) }.reduce(0, +)
        snap.dossiers = GC.dossiers.map { mdCount(graph.appendingPathComponent($0)) }.reduce(0, +)
        // Ядра — узлы сквозных тем; в счёт узлов входят тоже.
        let coreFiles = coreFolders.flatMap { folder -> [(String, URL)] in
            ((try? FileManager.default.contentsOfDirectory(
                at: graph.appendingPathComponent(folder),
                includingPropertiesForKeys: [.contentModificationDateKey]))?
                .filter { $0.pathExtension == "md" && !$0.lastPathComponent.hasPrefix(".") } ?? [])
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
