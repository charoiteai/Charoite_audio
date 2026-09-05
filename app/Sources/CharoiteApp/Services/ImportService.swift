import AppKit
import Foundation

#if os(macOS)

/// Папка импорта: упавшие в неё записи встреч сами становятся встречами графа.
///
/// Слежение — opt-in (тумблер в Настройках): раз в две минуты дешёвая
/// проверка по файловой системе, python (scripts/import_meeting.py --scan)
/// запускается только когда в папке реально появились поддерживаемые файлы.
/// Успешные импорты скрипт убирает в done/ и помечает сайдкаром; сбойные
/// остаются на виду с меткой ошибки и не пересканируются. Вкладка «Внешняя
/// запись» смотрит на ту же папку: перетащенный файл КОПИРУЕТСЯ сюда
/// (оригинал человека не трогаем), а копии в done/ живут `import_keep_days`
/// и уходят ретеншном скрипта (--prune) — он идёт после каждого скана и по
/// таймеру, даже когда слежение выключено (№166).
@MainActor
final class ImportService: ObservableObject {
    static let shared = ImportService()

    /// Папка, которую заводит вкладка, если в Настройках пусто.
    static let defaultDir = "~/Charoite_inbox"
    private static let dirKey = "charoite.importDir"

    @Published private(set) var status = ""
    @Published private(set) var items: [ImportItem] = []
    @Published private(set) var isScanning = false
    /// Хвост вывода последнего скана — для человека, который спросит «а что
    /// случилось»; скрипт печатает по восемь строк на файл.
    @Published private(set) var lastLog = ""

    private var timer: Timer?
    private var retentionTimer: Timer?
    private var proc: Process?
    private var pruneProc: Process?
    /// Файл добавили, пока скан ещё шёл: после него — ещё один проход.
    private var scanAgain = false

    /// Папка из Настроек, как её ввёл человек (тильда не раскрыта).
    static var configuredDir: String? {
        let d = UserDefaults.standard.string(forKey: dirKey) ?? ""
        return d.isEmpty ? nil : d
    }

    static func folderURL(_ dir: String) -> URL {
        URL(fileURLWithPath: (dir as NSString).expandingTildeInPath)
    }

    /// Вкладка без папки в Настройках заводит папку по умолчанию — и пишет её
    /// в те же Настройки, чтобы сканер и вкладка смотрели в одно место.
    func ensureFolder() -> String {
        if let d = Self.configuredDir { return d }
        let d = Self.defaultDir
        try? FileManager.default.createDirectory(at: Self.folderURL(d),
                                                 withIntermediateDirectories: true)
        UserDefaults.standard.set(d, forKey: Self.dirKey)
        return d
    }

    func enable(dir: String) {
        disable()
        scan(dir: dir)
        timer = Timer.scheduledTimer(withTimeInterval: 120, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in self?.scan(dir: dir) }
        }
    }

    func disable() {
        timer?.invalidate()
        timer = nil
    }

    /// Кнопка «Импортировать сейчас» — тот же прогон вне расписания.
    func scanNow(dir: String) { scan(dir: dir) }

    /// Ретеншн копий в done/ — сразу и раз в шесть часов. Живёт отдельно от
    /// слежения: срок «удалится через 2 дня» обещан и тому, кто слежение
    /// выключил и кладёт файлы руками через вкладку.
    func startRetention(dir: String) {
        prune(dir: dir)
        guard retentionTimer == nil else { return }
        retentionTimer = Timer.scheduledTimer(withTimeInterval: 6 * 3600, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, let dir = Self.configuredDir else { return }
                self.prune(dir: dir)
            }
        }
    }

    // MARK: - Список для вкладки

    /// Перечитать папку: корень (ждут / сбойные) и done/ (обработанные).
    func refresh(dir: String) {
        let folder = Self.folderURL(dir)
        var out: [ImportItem] = []
        let fm = FileManager.default
        let keys: [URLResourceKey] = [.fileSizeKey, .contentModificationDateKey,
                                      .attributeModificationDateKey, .isRegularFileKey,
                                      .isSymbolicLinkKey]
        let root = (try? fm.contentsOfDirectory(at: folder, includingPropertiesForKeys: keys,
                                                options: [.skipsHiddenFiles])) ?? []
        for url in root where ExternalRecordingPolicy.isSupported(url) {
            guard let v = try? url.resourceValues(forKeys: Set(keys)),
                  v.isRegularFile == true, v.isSymbolicLink != true else { continue }
            let marker = url.deletingLastPathComponent()
                .appendingPathComponent(".\(url.lastPathComponent).import-error")
            let phase: ImportItem.Phase
            if let data = try? Data(contentsOf: marker) {
                let m = try? JSONDecoder().decode(ExternalRecordingPolicy.ErrorMarker.self, from: data)
                phase = .failed(message: m?.message ?? "")
            } else {
                phase = .waiting
            }
            out.append(ImportItem(url: url, name: url.lastPathComponent,
                                  bytes: v.fileSize ?? 0,
                                  recorded: v.contentModificationDate ?? .distantPast,
                                  phase: phase))
        }
        let done = folder.appendingPathComponent("done", isDirectory: true)
        let processed = (try? fm.contentsOfDirectory(at: done, includingPropertiesForKeys: keys,
                                                     options: [.skipsHiddenFiles])) ?? []
        for url in processed {
            guard let v = try? url.resourceValues(forKeys: Set(keys)),
                  v.isRegularFile == true, v.isSymbolicLink != true else { continue }
            let sidecar = done.appendingPathComponent(".\(url.lastPathComponent).imported.json")
            let phase: ImportItem.Phase
            if let data = try? Data(contentsOf: sidecar),
               let s = try? JSONDecoder().decode(ExternalRecordingPolicy.Sidecar.self, from: data) {
                phase = .done(ExternalRecordingPolicy.imported(from: s))
            } else {
                let changed = v.attributeModificationDate ?? v.contentModificationDate ?? Date()
                phase = .legacy(deleteAt: ExternalRecordingPolicy.legacyDeleteAt(changed: changed))
            }
            out.append(ImportItem(url: url, name: url.lastPathComponent,
                                  bytes: v.fileSize ?? 0,
                                  recorded: v.contentModificationDate ?? .distantPast,
                                  phase: phase))
        }
        items = ExternalRecordingPolicy.sorted(out)
    }

    /// Файлы из перетаскивания или диалога: копия в папку импорта под
    /// свободным именем, затем скан. Оригинал остаётся там, где был.
    func add(urls: [URL], dir: String) {
        let folder = Self.folderURL(dir)
        let fm = FileManager.default
        try? fm.createDirectory(at: folder, withIntermediateDirectories: true)
        var copied = 0
        var skipped: [String] = []
        for url in urls {
            guard ExternalRecordingPolicy.isSupported(url) else {
                skipped.append(url.lastPathComponent)
                continue
            }
            // Файл уже лежит в папке импорта (перетащили из неё же) — не копия
            if url.deletingLastPathComponent().standardizedFileURL.path == folder.standardizedFileURL.path {
                copied += 1
                continue
            }
            let taken = Set((try? fm.contentsOfDirectory(atPath: folder.path)) ?? [])
            let name = ExternalRecordingPolicy.uniqueName(url.lastPathComponent, taken: taken)
            do {
                try fm.copyItem(at: url, to: folder.appendingPathComponent(name))
                copied += 1
            } catch {
                skipped.append("\(url.lastPathComponent): \(error.localizedDescription)")
            }
        }
        if !skipped.isEmpty {
            status = L.t("не взято: \(skipped.joined(separator: ", "))",
                         "not taken: \(skipped.joined(separator: ", "))",
                         "未接收：\(skipped.joined(separator: ", "))")
        }
        refresh(dir: dir)
        if copied > 0 { scan(dir: dir) }
    }

    /// «Повторить»: снять метку ошибки и прогнать скан ещё раз.
    func retry(_ item: ImportItem, dir: String) {
        let marker = item.url.deletingLastPathComponent()
            .appendingPathComponent(".\(item.name).import-error")
        try? FileManager.default.removeItem(at: marker)
        refresh(dir: dir)
        scan(dir: dir)
    }

    func reveal(_ item: ImportItem) {
        NSWorkspace.shared.activateFileViewerSelecting([item.url])
    }

    func openTranscript(_ item: ImportItem) {
        guard case .done(let imported) = item.phase, let path = imported.transcript else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }

    // MARK: - Процессы

    private func scan(dir: String) {
        guard proc == nil else {
            scanAgain = true            // предыдущий прогон ещё молотит
            return
        }
        let folder = Self.folderURL(dir)
        // Импорт перемещает файлы (успешные уезжают в done/), поэтому папка
        // должна существовать и быть именно папкой. Промежуточный путь,
        // случайно совпавший с реальной папкой мультимедиа, растаскивал бы
        // чужие файлы в граф.
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: folder.path, isDirectory: &isDir),
              isDir.boolValue else {
            status = L.t("папка импорта не найдена", "import folder not found", "未找到导入文件夹")
            return
        }
        refresh(dir: dir)
        let todo = items.filter { $0.phase == .waiting }
        guard !todo.isEmpty else { return }

        status = L.t("импорт: \(todo.count) файл(ов)…", "importing \(todo.count) file(s)…", "正在导入 \(todo.count) 个文件…")
        isScanning = true
        let p = Process()
        let root = AppSettings.charoiteRoot
        // `--`: путь папки выбирает человек, и argparse не должен прочитать
        // его как флаг, если имя начинается с дефиса (аудит 16.08, п.5).
        p.arguments = [AppSettings.scriptPath("scripts/import_meeting.py", root: root),
                       "--scan", "--", folder.path]
        AppSettings.preparePython(p, root: root)
        // Трубы читаем: скрипт печатает по восемь строк на файл, но
        // непрочитанная труба на 64 КБ подвесила бы импорт молча.
        let tail = LogTail()
        let out = Pipe(), err = Pipe()
        out.fileHandleForReading.readabilityHandler = { h in tail.append(h.availableData) }
        err.fileHandleForReading.readabilityHandler = { h in tail.append(h.availableData) }
        p.standardOutput = out
        p.standardError = err
        p.terminationHandler = { [weak self] proc in
            out.fileHandleForReading.readabilityHandler = nil
            err.fileHandleForReading.readabilityHandler = nil
            let code = proc.terminationStatus
            let text = tail.text
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.proc = nil
                self.isScanning = false
                self.lastLog = text
                self.status = code == 0
                    ? L.t("импорт завершён", "import finished", "导入完成")
                    : L.t("импорт: часть файлов не прошла", "import: some files failed", "导入：部分文件失败")
                self.refresh(dir: dir)
                if self.scanAgain {
                    self.scanAgain = false
                    self.scan(dir: dir)
                }
            }
        }
        do {
            try p.run()
            proc = p
        } catch {
            isScanning = false
            status = L.t("импорт не запустился: \(error.localizedDescription)", "import failed to start: \(error.localizedDescription)", "导入未能启动：\(error.localizedDescription)")
        }
    }

    /// `--prune`: копии в done/, отслужившие срок, уходят вместе с
    /// аудио-исходником в архиве. Решает скрипт — у него сайдкары.
    private func prune(dir: String) {
        guard pruneProc == nil else { return }
        let folder = Self.folderURL(dir)
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: folder.path, isDirectory: &isDir),
              isDir.boolValue else { return }
        let p = Process()
        let root = AppSettings.charoiteRoot
        p.arguments = [AppSettings.scriptPath("scripts/import_meeting.py", root: root),
                       "--prune", "--", folder.path]
        AppSettings.preparePython(p, root: root)
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        p.terminationHandler = { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.pruneProc = nil
                self.refresh(dir: dir)
            }
        }
        do {
            try p.run()
            pruneProc = p
        } catch {
            // ретеншн догонит на следующем скане — скрипт делает его сам
        }
    }
}

/// Хвост вывода дочернего процесса из readabilityHandler (фон) в главный
/// поток — под замком, без гонки за строку.
private final class LogTail: @unchecked Sendable {
    private let lock = NSLock()
    private var data = Data()
    private let limit = 16_000

    func append(_ chunk: Data) {
        guard !chunk.isEmpty else { return }
        lock.lock()
        data.append(chunk)
        if data.count > limit { data = data.suffix(limit) }
        lock.unlock()
    }

    var text: String {
        lock.lock()
        defer { lock.unlock() }
        return String(decoding: data, as: UTF8.self)
    }
}

#endif
