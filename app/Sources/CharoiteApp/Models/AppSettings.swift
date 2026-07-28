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
            return URL(fileURLWithPath: (v as NSString).expandingTildeInPath)
        }
        return nil
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
    private static func configValue(_ key: String) -> String? {
        let cfg = charoiteRoot.appendingPathComponent("config/config.yaml")
        guard let text = try? String(contentsOf: cfg, encoding: .utf8) else { return nil }
        var found: String?
        for line in text.split(separator: "\n") {
            let t = line.trimmingCharacters(in: .whitespaces)
            guard t.hasPrefix(key + ":") else { continue }
            var v = t.dropFirst(key.count + 1).trimmingCharacters(in: .whitespaces)
            if let hash = v.firstIndex(of: "#") { v = String(v[..<hash]).trimmingCharacters(in: .whitespaces) }
            v = v.trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
            if !v.isEmpty { found = v }
        }
        return found
    }
}
