import Foundation

/// Настройки приложения — всё локальное, персист в UserDefaults.
///
/// Никаких удалённых серверов по умолчанию: Ollama на этой машине, демон
/// суфлёра — из папки установки Charoite_audio, граф — из config.yaml суфлёра.
enum AppSettings {
    /// Папка установки Charoite_audio (там .venv, src/daemon.py, config/).
    static var charoiteRoot: URL {
        if let s = UserDefaults.standard.string(forKey: "charoite.root"), !s.isEmpty {
            return URL(fileURLWithPath: (s as NSString).expandingTildeInPath)
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Charoite_audio")
    }

    static var ollamaURL: String {
        let s = UserDefaults.standard.string(forKey: "charoite.ollama") ?? ""
        if let u = URL(string: s), u.host != nil { return s }
        return "http://localhost:11434"
    }

    /// Папка графа Obsidian — читается из config/config.yaml суфлёра
    /// (sufler.graph_dir), чтобы не настраивать одно и то же дважды.
    /// CHAROITE_GRAPH_DIR перекрывает конфиг (скрины/тесты на демо-графе).
    static var graphDir: URL? {
        if let env = ProcessInfo.processInfo.environment["CHAROITE_GRAPH_DIR"],
           !env.isEmpty {
            return URL(fileURLWithPath: (env as NSString).expandingTildeInPath)
        }
        if let v = configValue("graph_dir") {
            return resolvePath(v, relativeTo: charoiteRoot)
        }
        return nil
    }

    /// Относительный путь в config.yaml считается от корня установки — так же,
    /// как его видит Python-демон с `currentDirectoryURL = charoiteRoot`.
    /// Иначе документированный `graph_dir: demo/graph` работал в демоне, но
    /// приложение искало граф относительно случайной текущей папки .app.
    static func resolvePath(_ raw: String, relativeTo root: URL) -> URL {
        let expanded = (raw as NSString).expandingTildeInPath
        if (expanded as NSString).isAbsolutePath {
            return URL(fileURLWithPath: expanded)
        }
        return root.appendingPathComponent(expanded).standardizedFileURL
    }

    /// Язык интерфейса: та же настройка, что у документов встреч
    /// (sufler.language: ru|en|zh) — продукт переключается одним ключом,
    /// а не системной локалью. CHAROITE_UI_LANG перекрывает (скрины/тесты).
    static var uiLanguage: String {
        if let env = ProcessInfo.processInfo.environment["CHAROITE_UI_LANG"],
           ["ru", "en", "zh"].contains(env) { return env }
        if let v = configValue("language"), ["ru", "en", "zh"].contains(v) { return v }
        return "ru"
    }

    /// Лёгкий разбор одной строки config.yaml, без YAML-зависимости.
    /// Ключ ищется по всему файлу (stt.language и sufler.language совпадают
    /// по имени — берём последнее вхождение: sufler-секция ниже stt).
    static func configValue(_ key: String) -> String? {
        let cfg = charoiteRoot.appendingPathComponent("config/config.yaml")
        guard let text = try? String(contentsOf: cfg, encoding: .utf8) else { return nil }
        return parseValue(key, in: text)
    }

    /// Отделено от чтения файла ради тестов: разбор — чистая функция.
    ///
    /// Три ловушки, каждая давала молчаливый отказ.
    /// • CRLF: `.whitespaces` не включает `\r`, и путь графа получал хвостовой
    ///   возврат каретки — каталог «не существовал», архив молчал, а язык
    ///   «en\r» не совпадал ни с одним значением и откатывался на русский.
    ///   Файл с CRLF появляется сам: редактор на Windows, шара, копипаст.
    /// • `#` внутри значения: `graph_dir: "~/Vault #1"` резался до `~/Vault`.
    /// • Блочный скаляр (`key: >`) давал значение «>» — то есть относительный
    ///   путь от рабочего каталога приложения.
    static func parseValue(_ key: String, in text: String) -> String? {
        var found: String?
        for rawLine in text.split(whereSeparator: \.isNewline) {
            let t = rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
            guard t.hasPrefix(key + ":") else { continue }
            var v = t.dropFirst(key.count + 1)
                .trimmingCharacters(in: .whitespacesAndNewlines)
            guard !v.hasPrefix(">"), !v.hasPrefix("|") else { continue }  // блочный скаляр не наш случай
            let quoted = v.hasPrefix("\"") || v.hasPrefix("'")
            if !quoted, let hash = v.range(of: " #") {   // комментарий отделён пробелом
                v = String(v[..<hash.lowerBound])
            }
            v = v.trimmingCharacters(in: .whitespacesAndNewlines)
            v = v.trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
            v = v.trimmingCharacters(in: .whitespacesAndNewlines)
            if !v.isEmpty { found = v }
        }
        return found
    }

    /// Логический ключ конфига. Отсутствует или не распознан — `false`:
    /// для облачных разрешений безопасный дефолт — «нельзя».
    ///
    /// Список ровно тот, что питон считает разрешением: PyYAML разбирает
    /// true/yes/on в булево True, и `is True` в privacy.py его принимает.
    /// «1» PyYAML читает числом — питон отказывает, значит и здесь нельзя:
    /// тумблер в UI не имеет права показывать «включено» там, где демон
    /// скажет «нет».
    static func configFlag(_ key: String) -> Bool {
        ["true", "yes", "on"].contains((configValue(key) ?? "").lowercased())
    }

    /// Переписать логический ключ в config.yaml суфлёра.
    ///
    /// Приложение не тащит YAML-зависимость ради одного тумблера: правится
    /// ровно та строка, где ключ уже объявлен, остальной файл — включая
    /// комментарии, которыми конфиг и документирован — не трогается.
    /// Ключа в файле нет — не дописываем: значит конфиг не от этой версии,
    /// и молча менять его структуру опаснее, чем отказать.
    @discardableResult
    static func setConfigFlag(_ key: String, _ value: Bool) -> Bool {
        let cfg = charoiteRoot.appendingPathComponent("config/config.yaml")
        guard let text = try? String(contentsOf: cfg, encoding: .utf8) else { return false }

        var out: [String] = []
        var done = false
        for line in text.components(separatedBy: "\n") {
            let t = line.trimmingCharacters(in: .whitespaces)
            if !done, t.hasPrefix(key + ":") {
                // отступ сохраняем — ключ живёт внутри секции
                let indent = String(line.prefix(while: { $0 == " " }))
                // хвостовой комментарий сохраняем: он объясняет смысл ключа
                var tail = ""
                if let hash = line.range(of: " #") { tail = String(line[hash.lowerBound...]) }
                out.append("\(indent)\(key): \(value)\(tail)")
                done = true
            } else {
                out.append(line)
            }
        }
        guard done else { return false }
        do {
            try out.joined(separator: "\n").write(to: cfg, atomically: true, encoding: .utf8)
            return true
        } catch {
            return false
        }
    }
}
