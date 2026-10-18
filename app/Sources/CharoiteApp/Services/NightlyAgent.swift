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
    /// Две копии сразу launchd не запускает — в пределах одной метки.
    static let slots: [(hour: Int, minute: Int)] = [(19, 0), (20, 0), (21, 0), (4, 15)]

    /// Расписание прежнего приложения — единственное, что оно писало.
    static let legacySlots: [(hour: Int, minute: Int)] = [(4, 15)]

    /// Умеет ли скрипт гейт слотов. Код в папке данных бывает старше
    /// приложения (DMG поверх прежнего клона): четыре слота без гейта —
    /// четыре полных ночи с облачными шагами (финальный Opus №630). Гейт —
    /// два файла: `nightly.sh` читает `CHAROITE_SLOT` и зовёт `night_gate.py`,
    /// а сбой гейта даёт `go` — нужны оба. Байты, не текст: bash исполнит и
    /// скрипт не в UTF-8 (круг 14). Скрипт должен и звать гейт, и гейт —
    /// не пустой файл: пустой отвечает «go» в каждый слот (круг 15).
    /// Скрипта нет — не судим: установка вернёт `.noScript`.
    static func knowsSlots(_ script: String) -> Bool {
        let fm = FileManager.default
        guard fm.fileExists(atPath: script) else { return true }
        let gate = URL(fileURLWithPath: script).deletingLastPathComponent().appendingPathComponent("night_gate.py")
        guard let size = (try? fm.attributesOfItem(atPath: gate.path))?[.size] as? Int, size > 0,
              let data = fm.contents(atPath: script) else { return false }
        return ["CHAROITE_SLOT", "night_gate.py"].allSatisfy { data.range(of: Data($0.utf8)) != nil }
    }

    /// Слоты, которые этот скрипт выдержит: с гейтом — вечер и утро, без — прежний 04:15.
    static func slots(for script: String) -> [(hour: Int, minute: Int)] {
        knowsSlots(script) ? slots : legacySlots
    }

    /// «19:00, 20:00, 21:00, 04:15» — для подписей, чтобы время не жило
    /// литералом в трёх языках.
    static var slotsText: String { text(of: slots) }

    static func text(of slots: [(hour: Int, minute: Int)]) -> String {
        slots.map { String(format: "%02d:%02d", $0.hour, $0.minute) }.joined(separator: ", ")
    }

    /// Что Настройки могут честно сказать об установленном агенте.
    enum Shown: Equatable {
        case unreadable          // файл есть, но не разбирается
        case disabled            // свой агент с `Disabled` и календарём «час:минута» — «Включить» его перепишет
        case foreignDisabled     // прочий агент с `Disabled` (чужой или свой с другим поводом) — только удаление
        case otherTrigger        // запуск помимо календаря (`RunAtLoad`, `StartInterval`…)
        case irreducible         // календарь не «час:минута»
        case ours                // наш скрипт, наши слоты и `CHAROITE_SLOT`
        case stale(String)       // наш скрипт, слоты или гейт прежней версии — миграция при старте
        case manual(String)      // чистые «час:минута», команда не наша
    }

    /// Ключи, по которым launchd запускает агента помимо календаря. Пустой
    /// словарь `Sockets`/`MachServices`/`LaunchEvents` повода не даёт (круг 10).
    private static func hasOtherTrigger(_ plist: [String: Any]) -> Bool {
        if plist["StartInterval"] != nil { return true }
        for key in ["LaunchEvents", "Sockets", "MachServices"] where plist[key] != nil {
            if let d = plist[key] as? [String: Any], d.isEmpty { continue }
            return true
        }
        for key in ["RunAtLoad", "StartOnMount"] where (plist[key] as? Bool) == true { return true }
        if let keep = plist["KeepAlive"], (keep as? Bool) != false { return true }
        for key in ["WatchPaths", "QueueDirectories"] where !((plist[key] as? [Any])?.isEmpty ?? true) {
            return true
        }
        return false
    }

    /// Целое plist, а не `<real>` и не `<true/>`: `as? Int` мостит и то, и другое.
    private static func plistInt(_ v: Any?) -> Int? {
        guard let n = v as? NSNumber, CFGetTypeID(n) == CFNumberGetTypeID(),
              !CFNumberIsFloatType(n) else { return nil }
        return n.intValue
    }

    /// Разбор установленного plist для подписи Настроек. launchd читает
    /// пропущенный Hour/Minute как «любой», Weekday/Day/Month сужают запуск,
    /// `StartInterval`/`RunAtLoad`/`KeepAlive`… запускают помимо календаря —
    /// такое время подписью не передать, умолчанием тем более (круги 7–8).
    /// «Наш скрипт» — `runsScript`, тот же признак, что у миграции; наш, но со
    /// слотами прежней версии — `.stale`, его приведёт `migrateIfStale` (круг 9).
    /// Ручной plist не мигрирует (круг 6), подпись показывает его слоты.
    static func shown(at url: URL = plistURL, script: String) -> Shown {
        guard let data = try? Data(contentsOf: url),
              let plist = try? PropertyListSerialization.propertyList(from: data, format: nil)
                  as? [String: Any] else { return .unreadable }
        return classify(plist, script: script)
    }

    /// Один ответ на «что за агент лежит в файле»: его читают подпись Настроек
    /// и миграция. Раньше миграция решала своим признаком и переписывала агент,
    /// который подпись называла ручным (`Weekday`, `RunAtLoad`, круг 10).
    static func classify(_ plist: [String: Any], script: String) -> Shown {
        let shown = schedule(plist, script: script)
        // «Включить» переписывает файл установкой — она только у своего агента;
        // чужой выключенный подписан выключенным, но не переписывается (круги 11–12).
        // «Свой» — по команде, а не по часам: свой скрипт с ручным часом тоже
        // включается кнопкой, а не только удаляется (круг 14).
        // Свой с `RunAtLoad`/`Weekday` — не «Включить»: установка оставила бы его
        // поводы запуска и переписала расписание (круг 16) — только удаление.
        guard (plist["Disabled"] as? Bool) == true else { return shown }
        switch shown {
        case .ours, .stale: return .disabled
        case .manual where runsScript(plist, script): return .disabled
        default: return .foreignDisabled
        }
    }

    private static func schedule(_ plist: [String: Any], script: String) -> Shown {
        guard !hasOtherTrigger(plist) else { return .otherTrigger }
        let raw = plist["StartCalendarInterval"]
        let items: [Any] = (raw as? [Any]) ?? (raw is [String: Any] ? [raw as Any] : [])
        var have: [(hour: Int, minute: Int)] = []
        for item in items {
            guard let d = item as? [String: Any], Set(d.keys) == ["Hour", "Minute"],
                  let h = plistInt(d["Hour"]), let m = plistInt(d["Minute"]),
                  (0...23).contains(h), (0...59).contains(m) else { return .irreducible }
            have.append((h, m))
        }
        guard !have.isEmpty else { return .irreducible }
        guard runsScript(plist, script) else { return .manual(text(of: have)) }
        let key = { (s: [(hour: Int, minute: Int)]) in s.map { $0.hour * 60 + $0.minute }.sorted() }
        let slot = (plist["EnvironmentVariables"] as? [String: Any])?["CHAROITE_SLOT"] as? String
        // «Прежнее» — только то, что писало приложение: 04:15 или наши слоты без
        // гейта (или наоборот, у скрипта без гейта). Иной набор поставил человек
        // по SETUP — он ручной, миграция его не трогает (финальный Opus №630).
        let ours = knowsSlots(script)
            ? (key(have) == key(slots) && slot == "1")
            : key(have) == key(legacySlots)
        if ours { return .ours }
        let written = [legacySlots, slots].contains { key($0) == key(have) }
        return written ? .stale(text(of: have)) : .manual(text(of: have))
    }

    static func job(script: String, logPath: String) -> [String: Any] {
        [
            "Label": label,
            "ProgramArguments": ["/bin/bash", script],
            "StartCalendarInterval": slots(for: script).map { ["Hour": $0.hour, "Minute": $0.minute] },
            "EnvironmentVariables": knowsSlots(script) ? ["CHAROITE_SLOT": "1"] : [String: String](),
            "StandardOutPath": logPath,
            "StandardErrorPath": logPath,
        ]
    }

    /// Установленный plist, приведённый к ожидаемому. Владеем только слотами,
    /// `CHAROITE_SLOT` и путями логов; остальное — как лежало. Свои ключи
    /// окружения (`CHAROITE_ROOT`, `NIGHTLY_MAX_H`…) человек вписывает руками по
    /// SETUP, и перезапись целиком уводила статус ночи в клон (выходной круг 2
    /// №630). Окружение — `[String: Any]`: одно `<integer>` среди строк роняло
    /// приведение всего словаря, и стирались все ключи (круг 3). Команда не
    /// нашей формы (`runsScript`) при явной установке становится нашей. Логи — всегда наши: у
    /// включивших ночь до 16.08 там общий /tmp (аудит 16.08, круг 3).
    static func reconciled(installed: [String: Any]?, expected: [String: Any]) -> [String: Any] {
        guard var out = installed else { return expected }
        // Установка — это включение: выключенный в файле агент кнопкой
        // «Включить» снова запускается; миграция `.disabled` не трогает (круг 10).
        // `Disabled=false` — уже включён: ключ остаётся, лишней миграции нет (круг 11).
        if (out["Disabled"] as? Bool) == true { out.removeValue(forKey: "Disabled") }
        for key in ["Label", "StartCalendarInterval", "StandardOutPath", "StandardErrorPath"] {
            out[key] = expected[key]
        }
        let want = (expected["ProgramArguments"] as? [String])?.last ?? ""
        // `Program` launchd исполняет вместо argv[0]: рядом с нашим argv он
        // запускал бы чужой скрипт (круги 4–5). Ни приложение, ни SETUP его не
        // пишут — при явной установке команда наша целиком.
        if !runsScript(out, want) {
            out["ProgramArguments"] = expected["ProgramArguments"]
            out.removeValue(forKey: "Program")
        }
        var env = out["EnvironmentVariables"] as? [String: Any] ?? [:]
        for (k, v) in expected["EnvironmentVariables"] as? [String: Any] ?? [:] { env[k] = v }
        out["EnvironmentVariables"] = env
        return out
    }

    /// Нужно ли переписать установленный plist.
    ///
    /// Только у агента, который запускает скрипт этой установки (пути — после
    /// разрешения симлинков, как `agentPointsElsewhere`): чужой путь — повод для
    /// предупреждения на «Сегодня», а не для молчаливой подмены. Ключ `Program`
    /// — ручная установка не нашего вида: молча не трогаем (круг 5). Пока ночь
    /// идёт, не трогаем: `unload` оборвал бы прогон. Отличие — весь словарь
    /// против `reconciled`: список ключей рядом с ним забывал ключ (круг 5).
    static func needsMigration(installed: [String: Any]?, expected: [String: Any],
                               nightRunning: Bool) -> Bool {
        guard let installed, !nightRunning,
              let want = (expected["ProgramArguments"] as? [String])?.last else { return false }
        // Мигрирует только то, что подпись называет нашим (круг 10).
        switch classify(installed, script: want) {
        case .ours, .stale: break
        default: return false
        }
        let target = reconciled(installed: installed, expected: expected)
        return !(target as NSDictionary).isEqual(to: installed)
    }

    /// Агент исполняет скрипт этой установки: `Program` нет (launchd исполнял бы
    /// его вместо argv[0], круги 4–5), а команда — та, что пишет `job()`:
    /// `/bin/bash <абсолютный путь скрипта>`. Последнего аргумента мало:
    /// `echo <скрипт>`, `bash -n <скрипт>`, `sh -c "…" <скрипт>` его не
    /// исполняют (круг 10). Неизвестная форма — не наша, по умолчанию безопасно.
    static func runsScript(_ plist: [String: Any], _ script: String) -> Bool {
        // Своим считаем ровно то, что пишет `job()`: обёртки и другие оболочки
        // со своими флагами разбирать не берёмся — такой агент ручной (круг 11).
        guard plist["Program"] == nil, let args = plist["ProgramArguments"] as? [String],
              args.count == 2, args[0] == "/bin/bash", args[1].hasPrefix("/") else { return false }
        return samePath(args[1], script)
    }

    static func samePath(_ a: String, _ b: String) -> Bool {
        URL(fileURLWithPath: a).resolvingSymlinksInPath().path
            == URL(fileURLWithPath: b).resolvingSymlinksInPath().path
    }

    enum InstallResult: Equatable {
        case done
        case noScript
        case failed(String)
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
        // «Идёт» — по правилу статуса «Сегодня»: зависший `running` старше
        // порога — уже прерванная ночь и миграцию не держит (круг 4, DS M6).
        var running = false
        if let status, case .running = NightlyStatus.from(json: status).state { running = true }
        guard needsMigration(installed: installed, expected: job(script: script, logPath: logPath),
                             nightRunning: running) else { return }
        switch install(root: root) {
        case .done: NSLog("ночь: агент приведён к слотам \(text(of: slots(for: script)))")
        case .noScript: NSLog("ночь: миграция агента — scripts/nightly.sh не найден")
        case .failed(let why): NSLog("ночь: миграция агента не удалась — \(why)")
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
