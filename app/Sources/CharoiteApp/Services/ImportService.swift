import Foundation

#if os(macOS)

/// Папка импорта: упавшие в неё записи встреч сами становятся встречами графа.
///
/// Строго opt-in (тумблер в Настройках). Раз в две минуты — дешёвая проверка
/// по файловой системе; python (scripts/import_meeting.py --scan) запускается
/// только когда в папке реально появились поддерживаемые файлы. Успешные
/// импорты скрипт сам убирает в done/, сбойные остаются на виду.
@MainActor
final class ImportService: ObservableObject {
    static let shared = ImportService()

    @Published private(set) var status = ""
    private var timer: Timer?
    private var proc: Process?

    /// Расширения зеркалят AUDIO|TEXT|SUBS скрипта — единственного судьи.
    private static let supported: Set<String> = [
        "m4a", "wav", "mp3", "aif", "aiff", "caf", "txt", "md", "vtt", "srt",
    ]

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

    private func scan(dir: String) {
        guard proc == nil else { return }   // предыдущий прогон ещё молотит
        let folder = URL(fileURLWithPath: (dir as NSString).expandingTildeInPath)
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
        let pending = (try? FileManager.default.contentsOfDirectory(
            at: folder, includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles])) ?? []
        let todo = pending.filter { Self.supported.contains($0.pathExtension.lowercased()) }
        guard !todo.isEmpty else { return }

        status = "импорт: \(todo.count) файл(ов)…"
        let p = Process()
        let root = AppSettings.charoiteRoot
        p.executableURL = AppSettings.pythonExecutable(root: root)
        p.arguments = [root.appendingPathComponent("scripts/import_meeting.py").path,
                       "--scan", folder.path]
        p.currentDirectoryURL = root
        p.standardOutput = Pipe()
        p.standardError = Pipe()
        p.terminationHandler = { [weak self] proc in
            let code = proc.terminationStatus
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.proc = nil
                self.status = code == 0 ? "импорт завершён" : "импорт: часть файлов не прошла"
            }
        }
        do {
            try p.run()
            proc = p
        } catch {
            status = "импорт не запустился: \(error.localizedDescription)"
        }
    }
}

#endif
