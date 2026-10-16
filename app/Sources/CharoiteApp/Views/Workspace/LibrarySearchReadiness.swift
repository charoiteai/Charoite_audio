import Foundation

#if os(macOS)

/// Готов ли поиск по архиву встреч. Незаданная или недоступная папка графа —
/// не «ничего не найдено»: экран называет причину. Доступность папки здесь не
/// проверяется: её узнаёт рабочая задача поиска (`MeetingSearch.searchLibraryAsync`)
/// там же, где читает архив, — не главный поток и не `body`.
enum LibrarySearchReadiness: Equatable {
    case notConfigured
    case ready(URL)
    /// Поиск пошёл, но папки нет или это не каталог (отключённый том, опечатка).
    case unavailable(URL)

    static func resolve(graphDir: URL?) -> LibrarySearchReadiness {
        guard let graphDir else { return .notConfigured }
        return .ready(graphDir)
    }
}

#endif
