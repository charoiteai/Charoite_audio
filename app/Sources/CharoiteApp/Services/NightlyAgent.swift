import Foundation

/// Владелец launchd-агента ночи `ai.charoite.nightly`: что в нём записано,
/// установка, снятие и приведение старого plist к текущему.
///
/// Раньше plist писала кнопка настроек и только при его отсутствии: у того,
/// кто включил ночь до вечерних слотов, после обновления приложения навсегда
/// оставался один 04:15 без `CHAROITE_SLOT` (выходной круг №630). Теперь
/// кнопка и старт приложения зовут один код.
enum NightlyAgent {
    static let label = "ai.charoite.nightly"

    static var plistURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/\(label).plist")
    }

    /// Слоты ночи. Один запуск в 04:15 терял ночь: агент стартовал в тёмном
    /// пробуждении закрытого ноутбука и засыпал через 45 с. Вечерние слоты дают
    /// ночи шанс, а `scripts/night_gate.py` (его зовёт nightly.sh при
    /// CHAROITE_SLOT=1) не даёт следующему слоту переделать успешную ночь.
    /// Две копии сразу launchd не запускает — проверять «идёт ли» не нужно.
    static let slots: [(hour: Int, minute: Int)] = [(19, 0), (20, 0), (21, 0), (4, 15)]

    /// «19:00, 20:00, 21:00, 04:15» — для подписей, чтобы время не жило
    /// литералом в трёх языках.
    static var slotsText: String {
        slots.map { String(format: "%02d:%02d", $0.hour, $0.minute) }.joined(separator: ", ")
    }

    static func job(script: String, logPath: String) -> [String: Any] {
        [
            "Label": label,
            "ProgramArguments": ["/bin/bash", script],
            "StartCalendarInterval": slots.map { ["Hour": $0.hour, "Minute": $0.minute] },
            "EnvironmentVariables": ["CHAROITE_SLOT": "1"],
            "StandardOutPath": logPath,
            "StandardErrorPath": logPath,
        ]
    }

    /// Нужно ли переписать установленный plist.
    ///
    /// Только расписание и окружение, и только у агента, который запускает
    /// скрипт этой установки: чужой путь — повод для предупреждения на
    /// «Сегодня» (`agentPointsElsewhere`), а не для молчаливой подмены. Пока
    /// ночь идёт, не трогаем: `unload` оборвал бы прогон.
    static func needsMigration(installed: [String: Any]?, expected: [String: Any],
                               nightRunning: Bool) -> Bool {
        guard let installed, !nightRunning else { return false }
        guard (installed["ProgramArguments"] as? [String])
                == (expected["ProgramArguments"] as? [String]) else { return false }
        for key in ["StartCalendarInterval", "EnvironmentVariables"] {
            let have = installed[key].map { $0 as AnyObject }
            let want = expected[key].map { $0 as AnyObject }
            if !(have?.isEqual(want) ?? (want == nil)) { return true }
        }
        return false
    }

    enum InstallResult: Equatable {
        case done
        case noScript
        case failed(String)
    }

    /// Пишет plist и перегружает агента: `load` поверх загруженной метки
    /// определение не заменяет, поэтому сначала `unload`.
    @discardableResult
    static func install(root: URL) -> InstallResult {
        let script = root.appendingPathComponent("scripts/nightly.sh")
        guard FileManager.default.fileExists(atPath: script.path) else { return .noScript }
        // Лог ночного прогона — рядом с данными, а не в общем /tmp: туда
        // печатаются вопросы бенча памяти, названия ядер и куски ответов из
        // архива встреч, а /tmp читает любая учётка машины (аудит 16.08).
        let logDir = root.appendingPathComponent("logs")
        FileManager.default.createPrivateDirectory(at: logDir)
        let logPath = logDir.appendingPathComponent("nightly.log").path
        FileManager.default.createPrivateFile(atPath: logPath)
        // Пути — настоящими узлами plist, а не интерполяцией в XML: каталог
        // установки с «&» или «<» в имени иначе ломает или подменяет файл.
        let job = job(script: script.path, logPath: logPath)
        do {
            let data = try PropertyListSerialization.data(
                fromPropertyList: job, format: .xml, options: 0)
            // ~/Library/LaunchAgents — общий каталог всех агентов пользователя,
            // не наш: создать при отсутствии, но права не ужимать (0700 ему
            // ставил createPrivateDirectory — второе мнение по #324, 16.08).
            try? FileManager.default.createDirectory(
                at: plistURL.deletingLastPathComponent(),
                withIntermediateDirectories: true)
            if FileManager.default.fileExists(atPath: plistURL.path) {
                launchctl(["unload", plistURL.path])
            }
            try data.write(to: plistURL, options: .atomic)
            launchctl(["load", plistURL.path])
            return .done
        } catch {
            return .failed(error.localizedDescription)
        }
    }

    static func uninstall() {
        launchctl(["unload", plistURL.path])
        try? FileManager.default.removeItem(at: plistURL)
    }

    /// Старт приложения: ночь включена прежней версией — привести plist к
    /// текущим слотам. Без агента ничего не ставит: включает человек.
    static func migrateIfStale(root: URL) {
        guard let data = try? Data(contentsOf: plistURL),
              let installed = try? PropertyListSerialization.propertyList(
                  from: data, format: nil) as? [String: Any] else { return }
        let script = root.appendingPathComponent("scripts/nightly.sh").path
        let logPath = root.appendingPathComponent("logs/nightly.log").path
        let status = (try? Data(contentsOf: root.appendingPathComponent("logs/nightly.json")))
            .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        let running = (status?["state"] as? String) == "running"
        if needsMigration(installed: installed, expected: job(script: script, logPath: logPath),
                          nightRunning: running) {
            install(root: root)
        }
    }

    private static func launchctl(_ args: [String]) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = args
        try? p.run()
        p.waitUntilExit()
    }
}
