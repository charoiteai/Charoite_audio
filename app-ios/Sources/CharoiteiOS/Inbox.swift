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

    /// Куда пишется ИДУЩАЯ запись. Documents, а не tmp: систему не волнует,
    /// что мы посреди встречи, — tmp она чистит когда захочет, и вместе с ним
    /// исчезал единственный экземпляр часового разговора.
    static var inProgress: URL {
        let d = outbox.appendingPathComponent("current", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }

    /// Уже уехавшие записи, которые ещё держим на телефоне.
    ///
    /// «iCloud принял» не значит «Mac получил»: 27.07 контейнер не
    /// материализовался на маке неделями. Пока копия лежит здесь, встречу
    /// можно отдать руками — кнопкой «Поделиться» или через «Файлы».
    static var sent: URL {
        let d = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Sent", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        return d
    }

    /// Сколько отправленных записей держим. Часовая встреча — десятки
    /// мегабайт; пять последних это страховка, а не второй архив.
    static let keepSent = 5

    /// Расширения, которые считаются записью. CAF — основной формат: он
    /// переживает обрыв (в отличие от M4A без атома `moov`), M4A оставлен
    /// для файлов, записанных прежними версиями.
    private static let audioExts: Set<String> = ["caf", "m4a"]

    static var queuedCount: Int {
        queued.count
    }

    /// Записи, ждущие отправки, — новые первыми.
    static var queued: [URL] {
        recordings(in: outbox)
    }

    /// Одна запись в очереди — всё, что нужно показать человеку.
    ///
    /// Одной серой строкой «в очереди: 6» отделаться нельзя: за ней могут
    /// стоять шесть свежих файлов, которые уедут через минуту, а могут —
    /// получасовая встреча недельной давности, про которую человек уверен,
    /// что она давно на Mac.
    struct Item: Identifiable, Equatable {
        let url: URL
        let recorded: Date
        let bytes: Int

        var id: URL { url }
        var name: String { Self.humanName(url) }
        var size: String { sizeText(url) }

        /// Сколько запись ждёт доставки.
        func waiting(since now: Date = Date()) -> TimeInterval {
            max(0, now.timeIntervalSince(recorded))
        }

        /// Ждёт дольше суток — знак, что доставка не работает, а не «сейчас уедет».
        func isStuck(since now: Date = Date()) -> Bool {
            waiting(since: now) > 24 * 3600
        }

        /// «Встреча», «Заметка», «Дневник» — по префиксу, который читает Mac.
        static func humanName(_ url: URL) -> String {
            let file = url.lastPathComponent
            if file.hasPrefix("note_") { return L.t("Заметка", "Note", "笔记") }
            if file.hasPrefix("diary_") { return L.t("Дневник", "Diary", "日记") }
            return L.t("Встреча", "Meeting", "会议")
        }
    }

    /// Очередь как список: что именно лежит, когда записано и сколько весит.
    static var queuedItems: [Item] {
        recordings(in: outbox).map { url in
            let values = try? url.resourceValues(forKeys: [.contentModificationDateKey,
                                                           .fileSizeKey])
            return Item(url: url,
                        recorded: values?.contentModificationDate ?? .distantPast,
                        bytes: values?.fileSize ?? 0)
        }
    }

    /// Последняя запись — из очереди или из уже отправленных.
    ///
    /// Пока файл заперт внутри приложения, у человека нет ни одного способа
    /// достать его руками, если iCloud молчит. Отсюда кнопка «Поделиться» на
    /// экране и `UIFileSharingEnabled` в `project.yml` (сам Info.plist
    /// генерируется xcodegen и в репозитории не хранится — правка прямо в нём
    /// стирается следующей генерацией).
    ///
    /// Смотрит в обе папки намеренно: искать только в очереди значило бы, что
    /// кнопка есть ровно тогда, когда доставка сломалась, — то есть исчезает
    /// в тот момент, когда всё прошло штатно, и человек её просто не находит.
    static var lastRecording: URL? {
        recordings(in: outbox, sent).first
    }

    private static func recordings(in dirs: URL...) -> [URL] {
        let fm = FileManager.default
        return dirs
            .flatMap { (try? fm.contentsOfDirectory(
                at: $0, includingPropertiesForKeys: [.contentModificationDateKey])) ?? [] }
            .filter { audioExts.contains($0.pathExtension) }
            .sorted { modified($0) > modified($1) }
    }

    private static func modified(_ url: URL) -> Date {
        (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?
            .contentModificationDate ?? .distantPast
    }

    /// Человеческий размер файла для подписи под кнопкой.
    static func sizeText(_ url: URL) -> String {
        let bytes = (try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0
        // Единицы — на языке приложения: ByteCountFormatter берёт локаль
        // устройства, и в русском интерфейсе выходило «106 KB» рядом с
        // подписью «Поделиться записью».
        return Int64(bytes).formatted(.byteCount(style: .file).locale(L.locale))
    }

    /// Записи, пережившие смерть приложения: их никто не закрыл и не поставил
    /// в очередь. Зовётся на старте — раньше такие файлы просто пропадали.
    static func rescueOrphans() {
        let fm = FileManager.default
        let left = (try? fm.contentsOfDirectory(at: inProgress, includingPropertiesForKeys: nil)) ?? []
        for f in left where audioExts.contains(f.pathExtension) {
            try? fm.moveItem(at: f, to: uniqueName(in: outbox, like: f))
        }
    }

    /// Свободное имя рядом с занятым. Затирать чужой файл нельзя нигде:
    /// ни в своей очереди, ни в папке импорта на Mac.
    private static func uniqueName(in dir: URL, like file: URL) -> URL {
        let fm = FileManager.default
        var candidate = dir.appendingPathComponent(file.lastPathComponent)
        let base = file.deletingPathExtension().lastPathComponent
        let ext = file.pathExtension
        var n = 1
        while fm.fileExists(atPath: candidate.path) {
            candidate = dir.appendingPathComponent("\(base)-\(n).\(ext)")
            n += 1
        }
        return candidate
    }

    static var folderChosen: Bool {
        destinationFolder() != nil
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
        guard let url = try? URL(resolvingBookmarkData: bm, bookmarkDataIsStale: &stale) else {
            // Закладка не разрешается (восстановление из бэкапа, папку удалили).
            // Забываем её, чтобы UI перестал рисовать «папка настроена» и позвал
            // выбрать заново: раньше folderChosen смотрел на наличие байтов, и
            // человек месяцами копил очередь, не понимая, почему ничего не едет.
            UserDefaults.standard.removeObject(forKey: bookmarkKey)
            return nil
        }
        if stale {
            // Обновлять закладку можно только внутри security scope — раньше
            // bookmarkData() вызывался снаружи, всегда падал в try? и закладка
            // оставалась протухшей навсегда.
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            if let fresh = try? url.bookmarkData() {
                UserDefaults.standard.set(fresh, forKey: bookmarkKey)
            }
        }
        return url
    }

    /// Файл — в очередь, затем попытка доставки всей очереди.
    static func deliver(_ file: URL, status: @MainActor @escaping (String) -> Void) async {
        let fm = FileManager.default
        do {
            try fm.moveItem(at: file, to: uniqueName(in: outbox, like: file))
        } catch {
            // Раньше ошибка глушилась `try?`, и файл оставался в tmp — то есть
            // терялся при первой же уборке системы. Молчать здесь нельзя.
            await status(L.t("Не удалось поставить запись в очередь: \(error.localizedDescription)",
                             "Could not queue the recording: \(error.localizedDescription)",
                             "无法将录音加入队列：\(error.localizedDescription)"))
            return
        }
        await flush(status: status)
    }

    /// Дослать всё из очереди. Вызывается на старте и после каждого стопа.
    ///
    /// Публикация атомарная: копируем под `.part` и переименовываем. Сканер на
    /// Mac отбирает файлы по расширению и не проверяет, дописан ли файл, —
    /// попадание его таймера в окно копирования 43-мегабайтной встречи давало
    /// расшифровку половины разговора, после чего файл уезжал в done/ и
    /// повторный импорт становился невозможен.
    static func flush(status: @MainActor @escaping (String) -> Void) async {
        guard !flushing else { return }   // .task на вкладке и стоп могут прийти вместе
        flushing = true
        defer { flushing = false }

        let fm = FileManager.default
        let files = (try? fm.contentsOfDirectory(at: outbox, includingPropertiesForKeys: nil))?
            .filter { audioExts.contains($0.pathExtension) } ?? []
        guard !files.isEmpty else { return }
        guard let dir = destinationFolder() else {
            await status(L.t("Выберите папку iCloud (кнопка вверху) — записей в очереди: \(files.count)",
                             "Choose the iCloud folder (button above) — queued: \(files.count)",
                             "请选择 iCloud 文件夹（上方按钮）— 队列中：\(files.count)"))
            return
        }
        let scoped = dir.startAccessingSecurityScopedResource()
        defer { if scoped { dir.stopAccessingSecurityScopedResource() } }

        var delivered = 0
        var stuck: [String] = []
        for f in files {
            let dest = uniqueName(in: dir, like: f)
            let part = dest.appendingPathExtension("part")
            do {
                try? fm.removeItem(at: part)
                try fm.copyItem(at: f, to: part)
                try fm.moveItem(at: part, to: dest)     // публикация одним шагом
                // Из очереди убираем, только если iCloud принял файл. Раньше
                // «Уехало на Mac» печаталось по факту копирования — при
                // переполненном хранилище выгрузка отваливалась, а очередь
                // уже была пуста, и запись пропадала незаметно.
                let v = try? dest.resourceValues(forKeys: [.ubiquitousItemUploadingErrorKey])
                if let err = v?.ubiquitousItemUploadingError {
                    stuck.append(f.lastPathComponent)
                    await status(L.t("iCloud не принял \(f.lastPathComponent): \(err.localizedDescription)",
                                     "iCloud rejected \(f.lastPathComponent): \(err.localizedDescription)",
                                     "iCloud 拒绝了 \(f.lastPathComponent)：\(err.localizedDescription)"))
                    continue
                }
                retire(f)
                delivered += 1
            } catch {
                // continue, а не return: один сбойный файл не должен запирать
                // всю очередь, включая сегодняшнюю встречу.
                try? fm.removeItem(at: part)
                stuck.append(f.lastPathComponent)
                await status(L.t("Не отправилось (\(f.lastPathComponent)): \(error.localizedDescription)",
                                 "Failed (\(f.lastPathComponent)): \(error.localizedDescription)",
                                 "发送失败（\(f.lastPathComponent)）：\(error.localizedDescription)"))
            }
        }
        let left = queuedCount
        if left == 0 {
            await status(L.t("Уехало на Mac: \(delivered) файл(а)",
                             "Delivered to Mac: \(delivered)",
                             "已发送到 Mac：\(delivered)"))
        } else {
            await status(L.t("Отправлено \(delivered), в очереди \(left)",
                             "Sent \(delivered), queued \(left)",
                             "已发送 \(delivered)，队列中 \(left)"))
        }
    }

    /// Доставленный файл — из очереди в отправленные, лишнее удаляем.
    /// Очередь остаётся списком долгов, а копия последних встреч живёт на
    /// телефоне независимо от того, доехали ли они до Mac.
    private static func retire(_ file: URL) {
        let fm = FileManager.default
        guard (try? fm.moveItem(at: file, to: uniqueName(in: sent, like: file))) != nil else {
            // Переложить не вышло — из очереди файл убрать всё равно надо,
            // иначе он поедет в iCloud на каждом flush по кругу.
            try? fm.removeItem(at: file)
            return
        }
        let old = recordings(in: sent).dropFirst(keepSent)
        for f in old { try? fm.removeItem(at: f) }
    }

    private nonisolated(unsafe) static var flushing = false
}

/// Системный выбор папки (UIKit-мост). Пикер общий, папки разные: вкладка
/// записи выбирает им папку ДОСТАВКИ («Charoite Inbox»), вкладки встреч и
/// задач — папку ГРАФА (Obsidian-vault); какая нужна — объясняет вызывающий
/// экран своей подписью.
struct FolderPicker: UIViewControllerRepresentable {
    var onPick: (URL) -> Void

    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let p = UIDocumentPickerViewController(forOpeningContentTypes: [.folder])
        p.delegate = context.coordinator
        // Открываемся сразу в iCloud Drive. По умолчанию пикер показывает
        // «Недавние», а у нового пользователя там пусто: первый экран настройки
        // выглядит как пустой список, и до нужной папки надо ещё догадаться
        // дойти через «Обзор».
        p.directoryURL = FileManager.default
            .url(forUbiquityContainerIdentifier: nil)?
            .deletingLastPathComponent()
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
