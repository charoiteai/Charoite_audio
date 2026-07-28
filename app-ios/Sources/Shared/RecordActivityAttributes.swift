import ActivityKit
import Foundation

/// Live Activity записи: телефон лежит на столе, таймер живёт в
/// Dynamic Island и на локскрине — видно, что запись идёт, и её можно
/// не искать по приложениям. Файл общий для приложения и виджета.
struct RecordActivityAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        var startedAt: Date
    }
    /// «Встреча» / «Заметка» / «Дневник» — что именно пишем.
    var kind: String
}
