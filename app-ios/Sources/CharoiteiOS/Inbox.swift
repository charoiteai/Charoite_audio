import Foundation
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// Доставка записи на Mac.
///
/// Основной путь — папка, которую пользователь один раз выбрал в iCloud
/// Drive (security-scoped bookmark): тот же «Charoite Inbox», куда смотрит
/// папка импорта macOS-приложения. Обычный iCloud Drive синкается надёжно —
/// в отличие от контейнера приложения, который может неделями не
/// материализоваться на Mac (проверено 27.07).
///
/// Недоставленное не пропадает: очередь в Documents/Outbox (tmp система
/// чистит, Documents — нет), досылка при каждом запуске и каждом стопе.
enum Inbox {
    private static let bookmarkKey = "inbox.bookmark"

    static var outbox: URL {
        let d = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Outbox", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }

    static var queuedCount: Int {
        (try? FileManager.default.contentsOfDirectory(at: outbox, includingPropertiesForKeys: nil))?
            .filter { $0.pathExtension == "m4a" }.count ?? 0
    }

    static var folderChosen: Bool {
        UserDefaults.standard.data(forKey: bookmarkKey) != nil
    }

    /// Пользователь выбрал папку в «Файлах» — запоминаем закладку навсегда.
    static func saveFolder(_ url: URL) throws {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let bm = try url.bookmarkData()
        UserDefaults.standard.set(bm, forKey: bookmarkKey)
    }

    private static func destinationFolder() -> URL? {
        guard let bm = UserDefaults.standard.data(forKey: bookmarkKey) else { return nil }
        var stale = false
        guard let url = try? URL(resolvingBookmarkData: bm, bookmarkDataIsStale: &stale) else { return nil }
        if stale, let fresh = try? url.bookmarkData() {
            UserDefaults.standard.set(fresh, forKey: bookmarkKey)
        }
        return url
    }

    /// Файл — в очередь, затем попытка доставки всей очереди.
    static func deliver(_ file: URL, status: @MainActor @escaping (String) -> Void) async {
        let queued = outbox.appendingPathComponent(file.lastPathComponent)
        try? FileManager.default.moveItem(at: file, to: queued)
        await flush(status: status)
    }

    /// Дослать всё из очереди. Вызывается на старте и после каждого стопа.
    static func flush(status: @MainActor @escaping (String) -> Void) async {
        let files = (try? FileManager.default.contentsOfDirectory(
            at: outbox, includingPropertiesForKeys: nil))?.filter { $0.pathExtension == "m4a" } ?? []
        guard !files.isEmpty else { return }
        guard let dir = destinationFolder() else {
            await status("Выберите папку iCloud (кнопка вверху) — записей в очереди: \(files.count)")
            return
        }
        let scoped = dir.startAccessingSecurityScopedResource()
        defer { if scoped { dir.stopAccessingSecurityScopedResource() } }
        var sent = 0
        for f in files {
            let dest = dir.appendingPathComponent(f.lastPathComponent)
            do {
                if FileManager.default.fileExists(atPath: dest.path) {
                    try FileManager.default.removeItem(at: dest)
                }
                try FileManager.default.copyItem(at: f, to: dest)
                try FileManager.default.removeItem(at: f)
                sent += 1
            } catch {
                await status("Не отправилось (\(f.lastPathComponent)): \(error.localizedDescription)")
                return
            }
        }
        let left = queuedCount
        await status(left == 0 ? "Уехало на Mac: \(sent) файл(а)" : "Отправлено \(sent), в очереди \(left)")
    }
}

/// Системный выбор папки (UIKit-мост): один раз выбрать «Charoite Inbox»
/// в iCloud Drive — дальше всё само.
struct FolderPicker: UIViewControllerRepresentable {
    var onPick: (URL) -> Void

    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let p = UIDocumentPickerViewController(forOpeningContentTypes: [.folder])
        p.delegate = context.coordinator
        return p
    }

    func updateUIViewController(_ vc: UIDocumentPickerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onPick: onPick) }

    final class Coordinator: NSObject, UIDocumentPickerDelegate {
        let onPick: (URL) -> Void
        init(onPick: @escaping (URL) -> Void) { self.onPick = onPick }
        func documentPicker(_ controller: UIDocumentPickerViewController,
                            didPickDocumentsAt urls: [URL]) {
            if let u = urls.first { onPick(u) }
        }
    }
}
