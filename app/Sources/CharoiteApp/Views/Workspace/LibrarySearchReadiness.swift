import Foundation

#if os(macOS)

/// Готов ли поиск по архиву встреч и почему нет. Пустой результат без чтения
/// файлов — не «ничего не найдено»: экран называет причину и даёт действие.
enum LibrarySearchReadiness: Equatable {
    case notConfigured
    case unreachable(URL)
    case ready(URL)

    static func resolve(graphDir: URL?,
                        isDirectory: (URL) -> Bool = LibrarySearchReadiness.isDirectory) -> LibrarySearchReadiness {
        guard let graphDir else { return .notConfigured }
        return isDirectory(graphDir) ? .ready(graphDir) : .unreachable(graphDir)
    }

    static func isDirectory(_ url: URL) -> Bool {
        var directory: ObjCBool = false
        return FileManager.default.fileExists(atPath: url.path, isDirectory: &directory) && directory.boolValue
    }
}

#endif
