import SwiftUI

#if os(macOS)

/// Настройки: путь установки Charoite_audio и адрес Ollama. Всё локальное.
struct SettingsView: View {
    @AppStorage("charoite.root") private var root = ""
    @AppStorage("charoite.ollama") private var ollama = ""
    @AppStorage("charoite.calendarBriefs") private var calendarBriefs = false
    @AppStorage("charoite.importDir") private var importDir = ""
    @AppStorage("charoite.importWatch") private var importWatch = false
    @ObservedObject private var importer = ImportService.shared
    @State private var check = ""
    // Считается в фоне и только при смене пути: раньше это было вычисляемое
    // свойство, и полный обход графа (в iCloud — с докачкой выгруженных
    // файлов) выполнялся на КАЖДЫЙ символ, набранный в поле пути.
    @State private var graphStats = ""

    var body: some View {
        Form {
            Section(L.t("Подключение", "Connection", "连接")) {
                TextField(L.t("Папка Charoite_audio", "Charoite_audio folder", "Charoite_audio 文件夹"),
                          text: $root,
                          prompt: Text("~/Charoite_audio"))
                    .help(L.t("Где лежит установка: .venv, src/daemon.py, config/config.yaml", "Where the install lives: .venv, src/daemon.py, config/config.yaml", "安装位置：.venv、src/daemon.py、config/config.yaml"))
                TextField("Ollama",
                          text: $ollama,
                          prompt: Text("http://localhost:11434"))
                LabeledContent(L.t("Граф встреч", "Meeting graph", "会议图谱")) {
                    Text(AppSettings.graphDir?.path ?? L.t("не задан (graph_dir в config.yaml)", "not set (graph_dir in config.yaml)", "未设置（config.yaml 中的 graph_dir）"))
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
                // прозрачность: сразу видно, что граф живой и наполняется —
                // без этого «архив молчит» неотличим от «путь не тот»
                if !graphStats.isEmpty {
                    LabeledContent(L.t("В графе", "In the graph", "图谱中")) {
                        Text(graphStats)
                            .foregroundStyle(.secondary)
                    }
                }
                HStack {
                    Button(L.t("Проверить", "Check", "检查")) { Task { await runCheck() } }
                    if !check.isEmpty {
                        Text(check).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            Section(L.t("Ночной цикл", "Nightly cycle", "夜间流程")) {
                LabeledContent(L.t("Пока вы спите", "While you sleep", "在你入睡时")) {
                    Text(nightlyInstalled
                         ? L.t("включён · 04:15 — ревизия ядер, утренний бриф, бенч памяти", "on · 04:15 — core review, morning brief, memory bench", "已开启 · 04:15 — 核心复审、晨间简报、记忆基准")
                         : L.t("выключен", "off", "已关闭"))
                        .foregroundStyle(.secondary)
                }
                HStack {
                    Button(nightlyInstalled ? L.t("Выключить", "Turn off", "关闭") : L.t("Включить", "Turn on", "开启")) {
                        nightlyInstalled ? nightlyDisable() : nightlyEnable()
                    }
                    if !nightlyNote.isEmpty {
                        Text(nightlyNote).font(.caption).foregroundStyle(.secondary)
                    }
                }
                Text(L.t("Ставит launchd-задачу на 04:15: Tier-3 ревизия ядер графа (с бэкапами), бриф _Сегодня.md и бенч качества памяти. Всё локально; лог в /tmp/charoite_nightly.log.",
                         "Installs a launchd job at 04:15: Tier-3 review of graph cores (with backups), the _Today.md brief and a memory-quality bench. All local; log in /tmp/charoite_nightly.log.",
                         "在 04:15 安装 launchd 任务：图谱核心的 Tier-3 复审（含备份）、_Today.md 简报与记忆质量基准。全部本地运行；日志见 /tmp/charoite_nightly.log。"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section(L.t("Импорт записей", "Recording import", "录音导入")) {
                TextField(L.t("Папка импорта", "Import folder", "导入文件夹"),
                          text: $importDir,
                          prompt: Text("~/Charoite_inbox"))
                    .help(L.t("Сюда кладут записи встреч: m4a/wav/mp3, txt/md, vtt/srt", "Drop meeting recordings here: m4a/wav/mp3, txt/md, vtt/srt", "把会议录音放在这里：m4a/wav/mp3、txt/md、vtt/srt"))
                    // Слежение перезапускаем по Enter, а не на каждый символ.
                    // Пока стоял onChange, набор пути «~/Downloads/meetings»
                    // на промежуточном «~/Downloads» запускал сканер: чужие
                    // медиафайлы из Загрузок превращались во встречи графа и
                    // физически уезжали в done/. Перемещение пользовательских
                    // данных по опечатке — не та цена за удобство.
                    .onSubmit {
                        if importWatch { importer.enable(dir: importDir) }
                    }
                Toggle(L.t("Следить за папкой", "Watch the folder", "监视文件夹"), isOn: $importWatch)
                    .disabled(importDir.isEmpty)
                    .onChange(of: importWatch) { _, on in
                        on ? importer.enable(dir: importDir) : importer.disable()
                    }
                HStack {
                    Button(L.t("Импортировать сейчас", "Import now", "立即导入")) { importer.scanNow(dir: importDir) }
                        .disabled(importDir.isEmpty)
                    if !importer.status.isEmpty {
                        Text(importer.status).font(.caption).foregroundStyle(.secondary)
                    }
                }
                Text(L.t("Упавший в папку файл станет встречей графа: стенограмма, протокол, задачи, узлы. Готовые уходят в done/. Локально.",
                         "A file dropped here becomes a meeting in the graph: transcript, minutes, tasks, nodes. Processed ones move to done/. Locally.",
                         "放入此处的文件会成为图谱中的一场会议：逐字稿、纪要、任务、节点。处理完的移入 done/。全部本地。"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section(L.t("Календарь", "Calendar", "日历")) {
                Toggle(L.t("Предлагать бриф к ближайшей встрече", "Offer a brief for the next meeting", "为下一场会议提供简报"), isOn: $calendarBriefs)
                    .onChange(of: calendarBriefs) { _, on in
                        on ? CalendarService.shared.enable() : CalendarService.shared.disable()
                    }
                Text(L.t("Читает только название и время ближайшего события — для кнопки «Бриф» перед встречей. Локально, ничего не пишет.",
                         "Reads only the title and time of the next event — for the Brief button before a meeting. Local, write-free.",
                         "仅读取下一个日程的标题与时间——用于会前的「简报」按钮。本地运行，不做任何写入。"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section {
                Text(L.t("Всё работает локально: аудио, распознавание, модели, граф. Ничего не покидает этот Mac.",
                         "Everything runs locally: audio, recognition, models, graph. Nothing leaves this Mac.",
                         "一切都在本地运行：音频、识别、模型、图谱。没有任何内容离开这台 Mac。"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .frame(width: 440)
        .navigationTitle(L.t("Настройки", "Settings", "设置"))
        // Пересчёт только когда путь установки действительно сменился, и не
        // на главном потоке: обход графа в iCloud занимает секунды.
        .task(id: root) {
            // nonisolated-функция в detached-задаче: на Swift 5.10 (Xcode 15.4,
            // раннер CI) computeGraphStats внутри View считается async-вызовом
            // и требует await, на более новом компиляторе — нет. Явная
            // nonisolated-обёртка снимает расхождение.
            let stats = await Self.graphStatsInBackground()
            graphStats = stats
        }
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
            nightlyNote = L.t("scripts/nightly.sh не найден — проверьте путь установки", "scripts/nightly.sh not found — check the install path", "未找到 scripts/nightly.sh — 请检查安装路径")
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
            nightlyNote = L.t("готово — первый прогон сегодня в 04:15", "done — first run today at 04:15", "完成 — 今天 04:15 首次运行")
        } catch {
            nightlyNote = L.t("не удалось: \(error.localizedDescription)", "failed: \(error.localizedDescription)", "失败：\(error.localizedDescription)")
        }
        nightlyTick += 1
    }

    private func nightlyDisable() {
        launchctl(["unload", nightlyPlistURL.path])
        try? FileManager.default.removeItem(at: nightlyPlistURL)
        nightlyNote = L.t("выключен", "off", "已关闭")
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
    /// Обёртка для .task: считает в фоне и возвращает готовую строку.
    private nonisolated static func graphStatsInBackground() async -> String {
        await Task.detached(priority: .utility) { computeGraphStats() }.value
    }

    private nonisolated static func computeGraphStats() -> String {
        guard let graph = AppSettings.graphDir,
              let walker = FileManager.default.enumerator(
                at: graph, includingPropertiesForKeys: [.contentModificationDateKey],
                options: [.skipsHiddenFiles]) else { return "" }
        var notes = 0
        var lastMeeting: String?
        for case let url as URL in walker where url.pathExtension == "md" {
            notes += 1
            let name = url.deletingPathExtension().lastPathComponent
            if url.deletingLastPathComponent().lastPathComponent.hasPrefix(L.t("Встречи", "Meetings", "会议")),
               name >= (lastMeeting ?? "") {
                lastMeeting = name
            }
        }
        guard notes > 0 else { return "" }
        var parts = [L.t("\(notes) заметок", "\(notes) notes", "\(notes) 条笔记")]
        if let m = lastMeeting { parts.append(L.t("последняя встреча \(String(m.prefix(10)))", "last meeting \(String(m.prefix(10)))", "最近会议 \(String(m.prefix(10)))")) }
        return parts.joined(separator: " · ")
    }

    /// Сколько файлов графа реально доступно поиску.
    ///
    /// Дефект, который это ловит, был невидим полностью: iCloud пометил папки
    /// «Люди», «Системы», «Встречи» флагом UF_HIDDEN, обходчик поиска их
    /// пропускал, и приложение честно отвечало «в памяти этого нет» про людей,
    /// с которыми встречи были на этой неделе. Ни ошибки, ни предупреждения —
    /// просто половина графа перестала существовать. Пока такие расхождения
    /// не показаны человеку, о них узнают случайно.
    private nonisolated static func countGraphFiles(_ graph: URL) -> (onDisk: Int, visible: Int) {
        let fm = FileManager.default
        var onDisk = 0, visible = 0
        // «На диске» — всё, кроме служебных папок с точкой в имени.
        guard let walker = fm.enumerator(at: graph, includingPropertiesForKeys: nil) else {
            return (0, 0)
        }
        for case let url as URL in walker where url.pathExtension == "md" {
            let service = url.pathComponents.contains { $0.hasPrefix(".") && $0.count > 1 }
            if service { continue }
            onDisk += 1
            let hidden = (try? url.resourceValues(forKeys: [.isHiddenKey]))?.isHidden ?? false
            if !hidden { visible += 1 }
        }
        return (onDisk, visible)
    }

    private func graphVisibility() -> String {
        guard let graph = AppSettings.graphDir else {
            return L.t("– граф не задан", "– graph not set", "– 未设置图谱")
        }
        let (onDisk, visible) = Self.countGraphFiles(graph)
        guard onDisk > 0 else { return L.t("✓ граф", "✓ graph", "✓ 图谱") }
        let hidden = onDisk - visible
        guard hidden > 0 else {
            return L.t("✓ граф: \(onDisk) заметок", "✓ graph: \(onDisk) notes", "✓ 图谱：\(onDisk) 条笔记")
        }
        // Поиск на флаг больше не смотрит, но человеку знать полезно: файлы с
        // ним не видны в Finder и могут пропасть из чужих инструментов.
        return L.t("✓ граф: \(onDisk) заметок, из них \(hidden) помечены скрытыми (iCloud)",
                   "✓ graph: \(onDisk) notes, \(hidden) flagged hidden by iCloud",
                   "✓ 图谱：\(onDisk) 条笔记，其中 \(hidden) 条被 iCloud 标记为隐藏")
    }

    private func runCheck() async {
        var parts: [String] = []
        let daemon = AppSettings.charoiteRoot.appendingPathComponent("src/daemon.py")
        parts.append(FileManager.default.fileExists(atPath: daemon.path)
                     ? L.t("✓ демон", "✓ daemon", "✓ 守护进程") : L.t("✗ демон не найден", "✗ daemon not found", "✗ 未找到守护进程"))
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
                if names.contains(where: { $0.hasPrefix("bge-m3") }) {
                    // видно не только «модель есть», но и что индекс реально построен
                    let indexed = await SemanticIndex.shared.count()
                    parts.append(indexed > 0
                                 ? L.t("✓ семантика: \(indexed) файлов в индексе", "✓ semantics: \(indexed) files indexed", "✓ 语义：已索引 \(indexed) 个文件")
                                 : L.t("✓ bge-m3 (индекс построится при первом поиске)", "✓ bge-m3 (index builds on first search)", "✓ bge-m3（首次搜索时建立索引）"))
                } else {
                    parts.append(L.t("– bge-m3 нет: ollama pull bge-m3", "– no bge-m3: ollama pull bge-m3", "– 缺少 bge-m3：ollama pull bge-m3"))
                }
            } else {
                parts.append(L.t("✗ Ollama не отвечает", "✗ Ollama not responding", "✗ Ollama 无响应"))
            }
        }
        parts.append(graphVisibility())
        check = parts.joined(separator: "  ")
    }
}

#endif
