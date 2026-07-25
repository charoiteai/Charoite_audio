import Foundation

/// История вопросов-ответов по архиву: переживает перезапуск приложения.
///
/// Формат — JSON в Application Support (как история чата); лимит 50
/// записей, свежие в конце. root — для тестов.
@MainActor
final class ArchiveHistoryStore: ObservableObject {
    static let shared = ArchiveHistoryStore()

    struct Entry: Codable, Equatable {
        let q: String
        let a: String
    }

    @Published private(set) var entries: [Entry] = []
    private var loaded = false
    private static let limit = 50

    private func storeURL(root: URL?) -> URL {
        let dir = root ?? FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Charoite", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("archive_history.json")
    }

    func load(root: URL? = nil) {
        guard !loaded || root != nil else { return }
        loaded = true
        guard let data = try? Data(contentsOf: storeURL(root: root)),
              let decoded = try? JSONDecoder().decode([Entry].self, from: data) else { return }
        entries = decoded
    }

    func append(q: String, a: String, root: URL? = nil) {
        load(root: root)
        entries.append(Entry(q: q, a: a))
        if entries.count > Self.limit { entries.removeFirst(entries.count - Self.limit) }
        if let data = try? JSONEncoder().encode(entries) {
            try? data.write(to: storeURL(root: root), options: .atomic)
        }
    }

    func clear(root: URL? = nil) {
        entries = []
        try? FileManager.default.removeItem(at: storeURL(root: root))
    }
}
