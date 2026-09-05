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
    /// Имена, под которые копии ещё только пишутся: два дропа Recording.m4a
    /// подряд не должны целиться в один путь (GLM r2 по #496).
    private var reserved = Set<String>()

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
        scan(dir: dir, settleAll: true)
        timer = Timer.scheduledTimer(withTimeInterval: 120, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in self?.scan(dir: dir, settleAll: true) }
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
        guard retentionTimer == nil else { return }   // уже идёт — вкладка не плодит процессы
        prune(dir: dir)
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
                                      .isRegularFileKey, .isSymbolicLinkKey]
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
                let imported = ExternalRecordingPolicy.imported(from: s)
                phase = s.legacy == true ? .legacy(deleteAt: imported.deleteAt) : .done(imported)
            } else {
                phase = .legacy(deleteAt: nil)
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
    /// Сама копия — в фоне: гигабайтная запись диктофона или ещё не
    /// скачанный из iCloud файл держали бы главный поток десятки секунд
    /// (GLM r1 по #496).
    func add(urls: [URL], dir: String) {
        let folder = Self.folderURL(dir)
        let fm = FileManager.default
        try? fm.createDirectory(at: folder, withIntermediateDirectories: true)
        var jobs: [(from: URL, to: URL)] = []
        var alreadyThere = 0
        var skipped: [String] = []
        var taken = Set((try? fm.contentsOfDirectory(atPath: folder.path)) ?? []).union(reserved)
        for url in urls {
            guard ExternalRecordingPolicy.isSupported(url) else {
                skipped.append(url.lastPathComponent)
                continue
            }
            // Файл уже лежит в папке импорта (перетащили из неё же) — не копия
            if url.deletingLastPathComponent().standardizedFileURL.path == folder.standardizedFileURL.path {
                alreadyThere += 1
                continue
            }
            let name = ExternalRecordingPolicy.uniqueName(url.lastPathComponent, taken: taken)
            taken.insert(name)
            reserved.insert(name)
            jobs.append((url, folder.appendingPathComponent(name)))
        }
        if !skipped.isEmpty {
            status = L.t("не взято: \(skipped.joined(separator: ", "))",
                         "not taken: \(skipped.joined(separator: ", "))",
                         "未接收：\(skipped.joined(separator: ", "))")
        }
        guard !jobs.isEmpty else {
            refresh(dir: dir)
            if alreadyThere > 0 { scan(dir: dir) }
            return
        }
        status = L.t("копирую \(jobs.count) файл(ов)…", "copying \(jobs.count) file(s)…", "正在复制 \(jobs.count) 个文件…")
        let names = jobs.map { $0.to.lastPathComponent }
        Task.detached(priority: .userInitiated) {
            var failures: [String] = []
            var copied: [URL] = []
            for job in jobs {
                // Публикация атомарная, как у доставки с iPhone: пишем под
                // скрытым именем с расширением, которого сканер не знает, и
                // переименовываем одним шагом — недописанный файл не
                // существует ни для вкладки, ни для скрипта.
                let part = job.to.deletingLastPathComponent()
                    .appendingPathComponent(".\(job.to.lastPathComponent).\(UUID().uuidString.prefix(8)).part")
                do {
                    try FileManager.default.copyItem(at: job.from, to: part)
                    Self.carryModificationDate(from: job.from, to: part)
                    try FileManager.default.moveItem(at: part, to: job.to)
                    copied.append(job.to)
                } catch {
                    try? FileManager.default.removeItem(at: part)
                    failures.append("\(job.from.lastPathComponent): \(error.localizedDescription)")
                }
            }
            await MainActor.run {
                self.copiesFinished(dir: dir, names: names, copied: copied, failures: failures)
            }
        }
    }

    /// Дата встречи — это mtime файла (скрипт берёт штамп из него), а
    /// copyItem её сохранять не обязан: трёхдневная запись с телефона стала
    /// бы встречей «сегодня в минуту дропа» (критика DS r1 по #496).
    nonisolated private static func carryModificationDate(from src: URL, to dst: URL) {
        guard let date = (try? src.resourceValues(forKeys: [.contentModificationDateKey]))?
            .contentModificationDate else { return }
        var values = URLResourceValues()
        values.contentModificationDate = date
        var target = dst
        try? target.setResourceValues(values)
    }

    private func copiesFinished(dir: String, names: [String], copied: [URL], failures: [String]) {
        reserved.subtract(names)
        // Папку сменили, пока шла копия: учёт — всегда, хвост для экрана —
        // только своему каталогу (GLM r3 по #496)
        guard dir == (Self.configuredDir ?? dir) else { return }
        if !failures.isEmpty {
            status = L.t("не скопировано: \(failures.joined(separator: ", "))",
                         "not copied: \(failures.joined(separator: ", "))",
                         "未复制：\(failures.joined(separator: ", "))")
        }
        // Свежая копия под именем старого сбоя — это и есть «повторить»:
        // метка ошибки не должна пережить файл, который её вызвал (DS r1)
        for url in copied {
            let marker = url.deletingLastPathComponent()
                .appendingPathComponent(".\(url.lastPathComponent).import-error")
            try? FileManager.default.removeItem(at: marker)
        }
        refresh(dir: dir)
        if !copied.isEmpty { scan(dir: dir) }
    }

    /// Дроп принят, но часть файлов источник не отдал (зависший провайдер,
    /// файл исчез между дропом и загрузкой) — сказать вслух, а не молчать.
    func reportNotLoaded(_ count: Int) {
        status = L.t("не загрузилось из дропа: \(count) файл(ов)",
                     "not loaded from the drop: \(count) file(s)",
                     "拖放中有 \(count) 个文件未能加载")
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

    /// Скан. `settleAll` — с тика слежения: чужая копия в папку (Finder,
    /// синк) не атомарна, и скрипт ждёт 30 с покоя размера у любого файла;
    /// кнопка и скан после дропа — сразу (копии вкладки опубликованы через
    /// .part). Гейта «пока идёт копия» нет намеренно: недописанный файл
    /// невидим по построению, а счётчик без сторожа мог зависнуть навсегда
    /// (критика GLM r3 по #496).
    private func scan(dir: String, settleAll: Bool = false) {
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
                       "--scan"] + (settleAll ? ["--settle-all"] : []) + ["--", folder.path]
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
            // Хвост, пришедший после последнего колбэка, — иначе в журнале
            // нет ни «не удался…», ни «ретеншн: удалено N» (GLM r1 по #496)
            tail.append(out.fileHandleForReading.readDataToEndOfFile())
            tail.append(err.fileHandleForReading.readDataToEndOfFile())
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
