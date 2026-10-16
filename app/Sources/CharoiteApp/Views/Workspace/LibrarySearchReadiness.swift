import Foundation

#if os(macOS)

/// Готов ли поиск по архиву встреч. Незаданная папка графа — не «ничего не
/// найдено»: экран называет причину. Доступность папки на диске здесь не
/// проверяется: `stat` уснувшего сетевого тома висит до таймаута ядра, а
/// надёжной проверки без владельца-сервиса у экрана нет.
enum LibrarySearchReadiness: Equatable {
    case notConfigured
    case ready(URL)

    static func resolve(graphDir: URL?) -> LibrarySearchReadiness {
        guard let graphDir else { return .notConfigured }
        return .ready(graphDir)
    }
}

#endif
