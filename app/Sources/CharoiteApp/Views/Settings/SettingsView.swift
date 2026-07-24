import SwiftUI

#if os(macOS)

/// Настройки: путь установки Charoite_audio и адрес Ollama. Всё локальное.
struct SettingsView: View {
    @AppStorage("charoite.root") private var root = ""
    @AppStorage("charoite.ollama") private var ollama = ""
    @State private var check = ""

    var body: some View {
        Form {
            Section("Подключение") {
                TextField("Папка Charoite_audio",
                          text: $root,
                          prompt: Text("~/Charoite_audio"))
                    .help("Где лежит установка: .venv, src/daemon.py, config/config.yaml")
                TextField("Ollama",
                          text: $ollama,
                          prompt: Text("http://localhost:11434"))
                LabeledContent("Граф встреч") {
                    Text(AppSettings.graphDir?.path ?? "не задан (graph_dir в config.yaml)")
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                HStack {
                    Button("Проверить") { Task { await runCheck() } }
                    if !check.isEmpty {
                        Text(check).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            Section {
                Text("Всё работает локально: аудио, распознавание, модели, граф. "
                     + "Ничего не покидает этот Mac.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .frame(width: 440)
        .navigationTitle("Настройки")
    }

    private func runCheck() async {
        var parts: [String] = []
        let daemon = AppSettings.charoiteRoot.appendingPathComponent("src/daemon.py")
        parts.append(FileManager.default.fileExists(atPath: daemon.path)
                     ? "✓ демон" : "✗ демон не найден")
        if let url = URL(string: AppSettings.ollamaURL + "/api/tags") {
            let cfg = URLSessionConfiguration.ephemeral
            cfg.connectionProxyDictionary = [:]
            cfg.timeoutIntervalForRequest = 4
            let ok = (try? await URLSession(configuration: cfg).data(from: url)) != nil
            parts.append(ok ? "✓ Ollama" : "✗ Ollama не отвечает")
        }
        parts.append(AppSettings.graphDir != nil ? "✓ граф" : "– граф не задан")
        check = parts.joined(separator: "  ")
    }
}

#endif
