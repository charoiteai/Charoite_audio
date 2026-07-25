import SwiftUI

#if os(macOS)

/// Настройки: путь установки Charoite_audio и адрес Ollama. Всё локальное.
struct SettingsView: View {
    @AppStorage("charoite.root") private var root = ""
    @AppStorage("charoite.ollama") private var ollama = ""
    @AppStorage("charoite.calendarBriefs") private var calendarBriefs = false
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
                // прозрачность: сразу видно, что граф живой и наполняется —
                // без этого «архив молчит» неотличим от «путь не тот»
                if !graphStats.isEmpty {
                    LabeledContent("В графе") {
                        Text(graphStats)
                            .foregroundStyle(.secondary)
                    }
                }
                HStack {
                    Button("Проверить") { Task { await runCheck() } }
                    if !check.isEmpty {
                        Text(check).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            Section("Календарь") {
                Toggle("Предлагать бриф к ближайшей встрече", isOn: $calendarBriefs)
                    .onChange(of: calendarBriefs) { _, on in
                        on ? CalendarService.shared.enable() : CalendarService.shared.disable()
                    }
                Text("Читает только название и время ближайшего события — "
                     + "для кнопки «Бриф» перед встречей. Локально, ничего не пишет.")
                    .font(.caption).foregroundStyle(.secondary)
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

    /// «N заметок · последняя встреча DD.MM» — по файловой системе, мгновенно.
    private var graphStats: String {
        guard let graph = AppSettings.graphDir,
              let walker = FileManager.default.enumerator(
                at: graph, includingPropertiesForKeys: [.contentModificationDateKey],
                options: [.skipsHiddenFiles]) else { return "" }
        var notes = 0
        var lastMeeting: String?
        for case let url as URL in walker where url.pathExtension == "md" {
            notes += 1
            let name = url.deletingPathExtension().lastPathComponent
            if url.deletingLastPathComponent().lastPathComponent.hasPrefix("Встречи"),
               name >= (lastMeeting ?? "") {
                lastMeeting = name
            }
        }
        guard notes > 0 else { return "" }
        var parts = ["\(notes) заметок"]
        if let m = lastMeeting { parts.append("последняя встреча \(String(m.prefix(10)))") }
        return parts.joined(separator: " · ")
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
