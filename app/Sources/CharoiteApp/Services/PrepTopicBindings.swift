import Foundation

/// User-confirmed search topics. Keys include the graph and calendar identity;
/// matching by event title would silently bind unrelated recurring meetings.
struct PrepTopicBindings {
    private var topics: [String: String]

    init(data: Data) {
        topics = (try? JSONDecoder().decode([String: String].self, from: data)) ?? [:]
    }

    var data: Data { (try? JSONEncoder().encode(topics)) ?? Data() }

    static func key(graph: URL, calendar: String, item: String) -> String? {
        guard !calendar.isEmpty, !item.isEmpty else { return nil }
        let fields = [graph.resolvingSymlinksInPath().path, calendar, item]
        guard let data = try? JSONEncoder().encode(fields) else { return nil }
        return String(data: data, encoding: .utf8)
    }

    func topic(key: String?, title: String) -> String {
        key.flatMap { topics[$0] } ?? PrepPolicy.titleQuery(title)
    }

    mutating func set(_ topic: String, key: String) {
        let clean = topic.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        topics[key] = clean.isEmpty ? nil : clean
    }
}
