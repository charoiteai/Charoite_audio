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
    static var graphDir: URL? {
        let cfg = charoiteRoot.appendingPathComponent("config/config.yaml")
        guard let text = try? String(contentsOf: cfg, encoding: .utf8) else { return nil }
        // лёгкий разбор одной строки, без YAML-зависимости
        for line in text.split(separator: "\n") {
            let t = line.trimmingCharacters(in: .whitespaces)
            if t.hasPrefix("graph_dir:") {
                var v = t.dropFirst("graph_dir:".count).trimmingCharacters(in: .whitespaces)
                v = v.trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
                guard !v.isEmpty else { return nil }
                return URL(fileURLWithPath: (v as NSString).expandingTildeInPath)
            }
        }
        return nil
    }
}
