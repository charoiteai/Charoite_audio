import Foundation

/// Доставка записи на Mac: файл кладётся в iCloud Drive
/// `Charoite Inbox` — ту самую папку импорта (Настройки → Импорт записей
/// в macOS-приложении). Дальше Mac делает всё сам.
enum Inbox {
    /// Контейнер приложения в iCloud Drive; папка видна в Файлах.
    static var folder: URL? {
        FileManager.default
            .url(forUbiquityContainerIdentifier: nil)?
            .appendingPathComponent("Documents/Charoite Inbox", isDirectory: true)
    }

    static func deliver(_ file: URL, status: @MainActor @escaping (String) -> Void) async {
        guard let dir = folder else {
            await status("iCloud недоступен — файл остался в приложении: \(file.lastPathComponent)")
            return
        }
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let dest = dir.appendingPathComponent(file.lastPathComponent)
            // setUbiquitous переносит файл в iCloud и отдаёт аплоад системе:
            // докачает даже после закрытия приложения
            try FileManager.default.setUbiquitous(true, itemAt: file, destinationURL: dest)
            await status("Уехало на Mac: \(dest.lastPathComponent)")
        } catch {
            await status("iCloud не принял: \(error.localizedDescription)")
        }
    }
}
