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
            Section("Ночной цикл") {
                LabeledContent("Пока вы спите") {
                    Text(nightlyInstalled
                         ? "включён · 04:15 — ревизия ядер, утренний бриф, бенч памяти"
                         : "выключен")
                        .foregroundStyle(.secondary)
                }
                HStack {
                    Button(nightlyInstalled ? "Выключить" : "Включить") {
                        nightlyInstalled ? nightlyDisable() : nightlyEnable()
                    }
                    if !nightlyNote.isEmpty {
                        Text(nightlyNote).font(.caption).foregroundStyle(.secondary)
                    }
                }
                Text("Ставит launchd-задачу на 04:15: Tier-3 ревизия ядер графа "
                     + "(с бэкапами), бриф _Сегодня.md и бенч качества памяти. "
                     + "Всё локально; лог в /tmp/charoite_nightly.log.")
                    .font(.caption).foregroundStyle(.secondary)
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

    // ─ Ночной цикл: launchd-plist одной кнопкой ─

    private var nightlyPlistURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/ai.charoite.nightly.plist")
    }

    private var nightlyInstalled: Bool {
        _ = nightlyTick   // перерисовка после включения/выключения
        return FileManager.default.fileExists(atPath: nightlyPlistURL.path)
    }

    @State private var nightlyTick = 0
    @State private var nightlyNote = ""

    private func nightlyEnable() {
        let script = AppSettings.charoiteRoot.appendingPathComponent("scripts/nightly.sh")
        guard FileManager.default.fileExists(atPath: script.path) else {
            nightlyNote = "scripts/nightly.sh не найден — проверьте путь установки"
            return
        }
        let plist = """
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0"><dict>
          <key>Label</key><string>ai.charoite.nightly</string>
          <key>ProgramArguments</key>
          <array><string>/bin/bash</string><string>\(script.path)</string></array>
          <key>StartCalendarInterval</key><dict><key>Hour</key><integer>4</integer><key>Minute</key><integer>15</integer></dict>
          <key>StandardOutPath</key><string>/tmp/charoite_nightly.log</string>
          <key>StandardErrorPath</key><string>/tmp/charoite_nightly.log</string>
        </dict></plist>
        """
        do {
            try FileManager.default.createDirectory(
                at: nightlyPlistURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            try plist.write(to: nightlyPlistURL, atomically: true, encoding: .utf8)
            launchctl(["load", nightlyPlistURL.path])
            nightlyNote = "готово — первый прогон сегодня в 04:15"
        } catch {
            nightlyNote = "не удалось: \(error.localizedDescription)"
        }
        nightlyTick += 1
    }

    private func nightlyDisable() {
        launchctl(["unload", nightlyPlistURL.path])
        try? FileManager.default.removeItem(at: nightlyPlistURL)
        nightlyNote = "выключен"
        nightlyTick += 1
    }

    private func launchctl(_ args: [String]) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = args
        try? p.run()
        p.waitUntilExit()
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
            if let (data, _) = try? await URLSession(configuration: cfg).data(from: url) {
                parts.append("✓ Ollama")
                // семантический слой поиска живёт на bge-m3 — покажем сразу,
                // стоит ли она, вместо молчаливой лексической деградации
                let names = ((try? JSONSerialization.jsonObject(with: data) as? [String: Any])
                    .flatMap { $0["models"] as? [[String: Any]] } ?? [])
                    .compactMap { $0["name"] as? String }
                parts.append(names.contains { $0.hasPrefix("bge-m3") }
                             ? "✓ bge-m3 (семантика)"
                             : "– bge-m3 нет: ollama pull bge-m3")
            } else {
                parts.append("✗ Ollama не отвечает")
            }
        }
        parts.append(AppSettings.graphDir != nil ? "✓ граф" : "– граф не задан")
        check = parts.joined(separator: "  ")
    }
}

#endif
