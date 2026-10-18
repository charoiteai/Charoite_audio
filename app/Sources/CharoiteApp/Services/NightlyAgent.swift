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

    static var agentsDir: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/LaunchAgents")
    }

    static var plistURL: URL { plistURL(in: agentsDir) }

    static func plistURL(in dir: URL) -> URL {
        dir.appendingPathComponent("\(label).plist")
    }

    /// Слоты ночи. Один запуск в 04:15 терял ночь: агент стартовал в тёмном
    /// пробуждении закрытого ноутбука и засыпал через 45 с. Вечерние слоты дают
    /// ночи шанс, а `scripts/night_gate.py` (его зовёт nightly.sh при
    /// CHAROITE_SLOT=1) не даёт следующему слоту переделать успешную ночь.
    /// Две копии сразу launchd не запускает — в пределах одной метки; второй
    /// метки на тот же скрипт `install` не допускает (`otherAgent`).
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

    /// Установленный plist, приведённый к ожидаемому. Владеем только слотами,
    /// `CHAROITE_SLOT` и путями логов; остальное — как лежало. Свои ключи
    /// окружения (`CHAROITE_ROOT`, `NIGHTLY_MAX_H`…) человек вписывает руками по
    /// SETUP, и перезапись целиком уводила статус ночи в клон (выходной круг 2
    /// №630). Окружение — `[String: Any]`: одно `<integer>` среди строк роняло
    /// приведение всего словаря, и стирались все ключи (круг 3). Обёртку
    /// (`caffeinate`, `arch`) в `ProgramArguments` не трогаем, если она
    /// запускает скрипт этой установки (круг 3). Логи — всегда наши: у
    /// включивших ночь до 16.08 там общий /tmp (аудит 16.08, круг 3).
    static func reconciled(installed: [String: Any]?, expected: [String: Any]) -> [String: Any] {
        guard var out = installed else { return expected }
        for key in ["Label", "StartCalendarInterval", "StandardOutPath", "StandardErrorPath"] {
            out[key] = expected[key]
        }
        let want = (expected["ProgramArguments"] as? [String])?.last ?? ""
        let ownWrapper = (out["ProgramArguments"] as? [String]).map {
            scriptPaths(in: $0).contains { samePath($0, want) }
        } ?? false
        if !ownWrapper { out["ProgramArguments"] = expected["ProgramArguments"] }
        var env = out["EnvironmentVariables"] as? [String: Any] ?? [:]
        for (k, v) in expected["EnvironmentVariables"] as? [String: Any] ?? [:] { env[k] = v }
        out["EnvironmentVariables"] = env
        return out
    }

    /// Нужно ли переписать установленный plist.
    ///
    /// Только у агента, который запускает скрипт этой установки (пути — после
    /// разрешения симлинков, как `agentPointsElsewhere`): чужой путь — повод для
    /// предупреждения на «Сегодня», а не для молчаливой подмены. Пока ночь идёт,
    /// не трогаем: `unload` оборвал бы прогон.
    static func needsMigration(installed: [String: Any]?, expected: [String: Any],
                               nightRunning: Bool) -> Bool {
        guard let installed, !nightRunning,
              let have = installed["ProgramArguments"] as? [String],
              let want = (expected["ProgramArguments"] as? [String])?.last,
              scriptPaths(in: have).contains(where: { samePath($0, want) }) else { return false }
        let target = reconciled(installed: installed, expected: expected)
        for key in ["StartCalendarInterval", "EnvironmentVariables", "StandardOutPath", "StandardErrorPath"] {
            let a = installed[key].map { $0 as AnyObject }
            let b = target[key].map { $0 as AnyObject }
            if !(a?.isEqual(b) ?? (b == nil)) { return true }
        }
        return false
    }

    static func samePath(_ a: String, _ b: String) -> Bool {
        URL(fileURLWithPath: a).resolvingSymlinksInPath().path
            == URL(fileURLWithPath: b).resolvingSymlinksInPath().path
    }

    /// Пути `nightly.sh` в аргументах агента: отдельным аргументом или словом
    /// внутри строки обёртки (`/bin/sh -c "… /x/scripts/nightly.sh --fast"`).
    static func scriptPaths(in args: [String]) -> [String] {
        args.flatMap { $0.split(whereSeparator: \.isWhitespace) }
            .map { String($0).trimmingCharacters(in: CharacterSet(charactersIn: "\"'")) }
            .filter { $0.hasSuffix("nightly.sh") }
    }

    /// Агенты каталога, которые запускают какой-то `nightly.sh`: plist и путь
    /// скрипта, наш plist первым. Один разбор на установку и на статус «Сегодня»:
    /// статус искал текстом по UTF-8, установка — `PropertyListSerialization`,
    /// и бинарный plist одна видела, а другая нет (выходной круг 3 №630).
    /// Выключенный (`Disabled`) агент ночь не ставит — не в счёт.
    static func nightlyAgents(in dir: URL) -> [(plist: URL, script: String)] {
        let ours = plistURL(in: dir).lastPathComponent
        guard let items = try? FileManager.default.contentsOfDirectory(
            at: dir, includingPropertiesForKeys: nil) else { return [] }
        let sorted = items.filter { $0.pathExtension == "plist" }.sorted {
            ($0.lastPathComponent == ours ? "" : $0.lastPathComponent)
                < ($1.lastPathComponent == ours ? "" : $1.lastPathComponent)
        }
        return sorted.compactMap { url in
            guard let data = try? Data(contentsOf: url),
                  let job = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
                  job["Disabled"] as? Bool != true,
                  let script = scriptPaths(in: job["ProgramArguments"] as? [String] ?? []).first
            else { return nil }
            return (url, script)
        }
    }

    /// Другой plist каталога, который запускает `nightly.sh` этой установки.
    /// launchd не пускает две копии только одной метки: агент под другой
    /// меткой (ручная установка по SETUP, своя обёртка) и наш шли бы в одни
    /// слоты двумя ночами по одному графу (выходной круг 2 №630).
    static func otherAgent(script: String, in dir: URL) -> URL? {
        let ours = plistURL(in: dir).lastPathComponent
        return nightlyAgents(in: dir).first {
            $0.plist.lastPathComponent != ours && samePath($0.script, script)
        }?.plist
    }

    enum InstallResult: Equatable {
        case done
        case noScript
        case failed(String)
        case otherAgent(String)   // имя plist, который уже ставит эту ночь
    }

    /// Пишет plist и перегружает агента: `load` поверх загруженной метки
    /// определение не заменяет, поэтому `unload` — но только когда новый файл
    /// уже лежит рядом. Раньше `unload` шёл до записи, и сбой записи (plist
    /// root-а, нет места) оставлял ночь выгруженной молча (выходной круг 2 №630).
    @discardableResult
    static func install(root: URL, agentsDir dir: URL = agentsDir,
                        launchctl run: ([String]) -> Void = launchctl) -> InstallResult {
        let script = root.appendingPathComponent("scripts/nightly.sh")
        guard FileManager.default.fileExists(atPath: script.path) else { return .noScript }
        let target = plistURL(in: dir)
        let existed = FileManager.default.fileExists(atPath: target.path)
        // Только при создании: свой агент уже стоит — переписать его не хуже,
        // чем оставить старые слоты, а отказ держал миграцию вечно (круг 3).
        if !existed, let other = otherAgent(script: script.path, in: dir) {
            return .otherAgent(other.lastPathComponent)
        }
        // Лог ночного прогона — рядом с данными, а не в общем /tmp: туда
        // печатаются вопросы бенча памяти, названия ядер и куски ответов из
        // архива встреч, а /tmp читает любая учётка машины (аудит 16.08).
        let logDir = root.appendingPathComponent("logs")
        FileManager.default.createPrivateDirectory(at: logDir)
        let logPath = logDir.appendingPathComponent("nightly.log").path
        FileManager.default.createPrivateFile(atPath: logPath)
        let installed = (try? Data(contentsOf: target)).flatMap {
            try? PropertyListSerialization.propertyList(from: $0, format: nil) as? [String: Any]
        }
        // Пути — настоящими узлами plist, а не интерполяцией в XML: каталог
        // установки с «&» или «<» в имени иначе ломает или подменяет файл.
        let job = reconciled(installed: installed,
                             expected: job(script: script.path, logPath: logPath))
        // Без суффикса .plist: launchd и поиск агентов временный файл не видят.
        let staged = dir.appendingPathComponent(".\(label).plist.new")
        do {
            let data = try PropertyListSerialization.data(
                fromPropertyList: job, format: .xml, options: 0)
            // ~/Library/LaunchAgents — общий каталог всех агентов пользователя,
            // не наш: создать при отсутствии, но права не ужимать (0700 ему
            // ставил createPrivateDirectory — второе мнение по #324, 16.08).
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            try data.write(to: staged, options: .atomic)
        } catch {
            try? FileManager.default.removeItem(at: staged)
            return .failed(error.localizedDescription)
        }
        if existed { run(["unload", target.path]) }
        if rename(staged.path, target.path) != 0 {
            let why = String(cString: strerror(errno))
            try? FileManager.default.removeItem(at: staged)
            if existed { run(["load", target.path]) }   // прежний агент — обратно
            return .failed(why)
        }
        run(["load", target.path])
        return .done
    }

    static func uninstall() {
        launchctl(["unload", plistURL.path])
        try? FileManager.default.removeItem(at: plistURL)
    }

    /// Старт приложения: ночь включена прежней версией — привести plist к
    /// текущим слотам. Без агента ничего не ставит: включает человек. Исход
    /// не глотаем: неудача — строкой в лог приложения.
    static func migrateIfStale(root: URL) {
        guard let data = try? Data(contentsOf: plistURL),
              let installed = try? PropertyListSerialization.propertyList(
                  from: data, format: nil) as? [String: Any] else { return }
        let script = root.appendingPathComponent("scripts/nightly.sh").path
        let logPath = root.appendingPathComponent("logs/nightly.log").path
        let status = (try? Data(contentsOf: root.appendingPathComponent("logs/nightly.json")))
            .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        let running = (status?["state"] as? String) == "running"
        guard needsMigration(installed: installed, expected: job(script: script, logPath: logPath),
                             nightRunning: running) else { return }
        switch install(root: root) {
        case .done: NSLog("ночь: агент приведён к слотам \(slotsText)")
        case .noScript: NSLog("ночь: миграция агента — scripts/nightly.sh не найден")
        case .failed(let why): NSLog("ночь: миграция агента не удалась — \(why)")
        case .otherAgent(let name): NSLog("ночь: миграция агента пропущена — ночь ставит и \(name)")
        }
    }

    static func launchctl(_ args: [String]) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = args
        try? p.run()
        p.waitUntilExit()
    }
}
