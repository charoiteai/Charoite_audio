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

    struct Core: Identifiable, Equatable {
        let name: String
        let status: String   // первая содержательная строка ядра; пусто — нет
        let updated: Date
        var id: String { name }
    }

    struct Snapshot: Equatable {
        var meetings = 0
        var nodes = 0
        var dossiers = 0
        var cores: [Core] = []
    }

    @Published private(set) var snapshot = Snapshot()
    private var scannedAt: Date?
    private var scanning = false

    /// Узловые папки графа — те же, что в MemoryScreenPolicy.kind(of:).
    nonisolated private static let nodeFolders = ["Люди", "Системы", "Команды", "Блокеры", "Модели"]
    nonisolated private static let dossierFolders = ["Досье", "Dossiers"]

    /// Освежить не чаще раза в минуту: appear/переключения дешёвые.
    func refresh() {
        if let at = scannedAt, Date().timeIntervalSince(at) < 60 { return }
        guard !scanning, let graph = AppSettings.graphDir else { return }
        scanning = true
        Task.detached(priority: .utility) {
            let snap = Self.scan(graph: graph)
            await MainActor.run {
                let service = GraphInventoryService.shared
                service.snapshot = snap
                service.scannedAt = Date()
                service.scanning = false
            }
        }
    }

    nonisolated private static func mdCount(_ dir: URL) -> Int {
        (try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil))?
            .filter { $0.pathExtension == "md" && !$0.lastPathComponent.hasPrefix(".") }
            .count ?? 0
    }

    nonisolated static func scan(graph: URL, coreLimit: Int = 4) -> Snapshot {
        var snap = Snapshot()
        snap.meetings = mdCount(graph.appendingPathComponent("Встречи"))
        snap.nodes = nodeFolders.map { mdCount(graph.appendingPathComponent($0)) }.reduce(0, +)
        snap.dossiers = dossierFolders.map { mdCount(graph.appendingPathComponent($0)) }.reduce(0, +)
        // Ядра — узлы сквозных тем; в счёт узлов входят тоже.
        let coresDir = graph.appendingPathComponent("Ядра")
        let coreFiles = (try? FileManager.default.contentsOfDirectory(
            at: coresDir, includingPropertiesForKeys: [.contentModificationDateKey]))?
            .filter { $0.pathExtension == "md" && !$0.lastPathComponent.hasPrefix(".") } ?? []
        snap.nodes += coreFiles.count
        let dated: [(URL, Date)] = coreFiles.map {
            ($0, (try? $0.resourceValues(forKeys: [.contentModificationDateKey])
                .contentModificationDate) ?? .distantPast)
        }
        snap.cores = dated.sorted { $0.1 > $1.1 }.prefix(coreLimit).map { url, date in
            Core(name: url.deletingPathExtension().lastPathComponent,
                 status: coreStatus(of: url), updated: date)
        }
        return snap
    }

    /// Строка состояния ядра: первая содержательная строка после заголовка —
    /// без разметки, обрезана до фразы. Файл читаем куском, не целиком.
    nonisolated static func coreStatus(of url: URL) -> String {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return "" }
        defer { try? handle.close() }
        let data = (try? handle.read(upToCount: 4096)) ?? Data()
        guard let head = String(data: data, encoding: .utf8) else { return "" }
        var inFrontmatter = false
        for raw in head.components(separatedBy: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line == "---" { inFrontmatter.toggle(); continue }
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
