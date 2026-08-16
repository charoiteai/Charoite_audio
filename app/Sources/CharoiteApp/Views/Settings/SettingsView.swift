import SwiftUI

#if os(macOS)

/// Настройки: путь установки Charoite_audio и адрес Ollama. Всё локальное.
struct SettingsView: View {
    @AppStorage("charoite.root") private var root = ""
    // Отсутствие ключа = прежний договор #328 (явный путь с кодом = код оттуда),
    // поэтому дефолт true; тумблер показывается только там, где есть выбор.
    @AppStorage("charoite.codeFromRoot") private var codeFromRoot = true
    @AppStorage("charoite.ollama") private var ollama = ""
    @AppStorage("charoite.calendarBriefs") private var calendarBriefs = false
    @AppStorage("charoite.importDir") private var importDir = ""
    @AppStorage("charoite.importWatch") private var importWatch = false
    /// Не @AppStorage: источник правды — config.yaml, его читает ночной скрипт.
    /// Здесь только отражение, начальное значение берётся из файла в onAppear.
    @State private var cloudEditGraph = false
    @State private var cloudEditNote = ""
    @ObservedObject private var importer = ImportService.shared
    @ObservedObject private var launchAtLogin = LaunchAtLoginService.shared
    @State private var check = ""
    // Считается в фоне и только при смене пути: раньше это было вычисляемое
    // свойство, и полный обход графа (в iCloud — с докачкой выгруженных
    // файлов) выполнялся на КАЖДЫЙ символ, набранный в поле пути.
    @State private var graphStats = ""

    /// В выбранной папке лежит код демона — тогда и только тогда есть что решать.
    private var localCodeAtRoot: Bool {
        guard let chosen = AppSettings.explicitRoot else { return false }
        return FileManager.default.fileExists(atPath: chosen.appendingPathComponent("src/daemon.py").path)
    }

    var body: some View {
        Form {
            Section(L.t("Подключение", "Connection", "连接")) {
                TextField(L.t("Папка данных", "Data folder", "数据文件夹"),
                          text: $root,
                          prompt: Text("~/Charoite_audio"))
                    .help(L.t("Где лежат данные: config/config.yaml, transcripts/, models/ (для установки из клона — сам клон с .venv и src/daemon.py)", "Where the data lives: config/config.yaml, transcripts/, models/ (for a cloned install — the clone itself with .venv and src/daemon.py)", "数据所在位置：config/config.yaml、transcripts/、models/（克隆安装则为含 .venv 与 src/daemon.py 的克隆目录）"))
                // Пустое поле — не «нигде»: при коде в приложении данные идут в
                // Application Support, при запуске из клона — в сам клон.
                // Человек должен видеть, куда именно, не заглядывая в код.
                Text(L.t("Сейчас: \(AppSettings.charoiteRoot.path)", "Now: \(AppSettings.charoiteRoot.path)", "当前：\(AppSettings.charoiteRoot.path)"))
                    .font(.caption).foregroundStyle(.secondary)
                if AppSettings.legacyCloneAwaitsChoice {
                    HStack(spacing: 8) {
                        Text(L.t("Найден клон ~/Charoite_audio — при коде в приложении он не берётся сам", "Found the ~/Charoite_audio clone — with bundled code it is not adopted automatically", "发现 ~/Charoite_audio 克隆 — 内置代码时不会自动采用"))
                            .font(.caption).foregroundStyle(.secondary)
                        Button(L.t("Использовать для данных", "Use for data", "用作数据")) {
                            AppSettings.adoptLegacyCloneAsDataRoot()
                            root = "~/Charoite_audio"
                        }
                        .charoite(.link, .s)
                    }
                }
                // Явная папка данных ещё не разрешение исполнять её код с
                // правами приложения — это отдельное, видимое решение.
                if AppSettings.codeIsEmbedded, !root.isEmpty, localCodeAtRoot {
                    Toggle(isOn: $codeFromRoot) {
                        Text(L.t("Запускать код демона из этой папки (разработка)", "Run the daemon code from this folder (development)", "从此文件夹运行守护进程代码（开发）"))
                    }
                    .help(L.t("Выключено — код из приложения (подписанный бандл). Включено — src/daemon.py из выбранной папки, с правами приложения на микрофон и экран.", "Off — code from the app (signed bundle). On — src/daemon.py from the chosen folder, with the app's microphone and screen permissions.", "关闭 — 使用应用（已签名包）内的代码。开启 — 使用所选文件夹中的 src/daemon.py，并拥有应用的麦克风与屏幕权限。"))
                }
                TextField("Ollama",
                          text: $ollama,
                          prompt: Text("http://localhost:11434"))
                // Адрес не на этой машине отвергнут — но молча подменить его
                // значило бы сделать вид, что настройка применена. Человек
                // должен видеть, что запросы идут локально и почему.
                if let rejected = AppSettings.ollamaURLRejection {
                    Label {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(L.t("Адрес \(rejected.url) не используется — работаем локально",
                                     "Address \(rejected.url) is not used — running locally",
                                     "地址 \(rejected.url) 未使用 — 本地运行"))
                                .font(.callout)
                            Text(rejected.reason)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    } icon: {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                    }
                }
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
                        .charoite(.regular, .s)
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
                    .charoite(.regular, .s)
                    if !nightlyNote.isEmpty {
                        Text(nightlyNote).font(.caption).foregroundStyle(.secondary)
                    }
                }
                Text(L.t("Ставит launchd-задачу на 04:15: Tier-3 ревизия ядер графа (с бэкапами), досье по темам, бриф _Сегодня.md и бенч качества памяти. Всё локально; лог в /tmp/charoite_nightly.log.",
                         "Installs a launchd job at 04:15: Tier-3 review of graph cores (with backups), topic dossiers, the _Today.md brief and a memory-quality bench. All local; log in /tmp/charoite_nightly.log.",
                         "在 04:15 安装 launchd 任务：图谱核心的 Tier-3 复审（含备份）、主题档案、_Today.md 简报与记忆质量基准。全部本地运行；日志见 /tmp/charoite_nightly.log。"))
                    .font(.caption).foregroundStyle(.secondary)

                Toggle(L.t("Разрешить облаку править досье",
                           "Let the cloud edit dossiers",
                           "允许云端修改档案"), isOn: $cloudEditGraph)
                    .onChange(of: cloudEditGraph) { _, on in
                        // Пишем в config.yaml, а не в UserDefaults: разрешение
                        // спрашивает ночной скрипт, и знать он должен одно место.
                        if !AppSettings.setConfigFlag("cloud_edit_graph", on) {
                            cloudEditNote = L.t("не нашёл ключ в config.yaml",
                                                "key not found in config.yaml",
                                                "在 config.yaml 中未找到该键")
                            cloudEditGraph = !on
                        } else {
                            cloudEditNote = ""
                        }
                    }
                if !cloudEditNote.isEmpty {
                    Text(cloudEditNote).font(.caption).foregroundStyle(.orange)
                }
                Text(L.t("Ночью локальная модель собирает досье по темам. С этой галочкой облачная модель проходит вторым и правит их сама: замечает отменённые решения, истёкшие сроки, расхождения между источниками. Без галочки — только пишет отчёт, а правите вы. Стенограммы, минутки и раздел «Правки автора» не трогаются никогда; перед каждой правкой — бэкап.",
                         "At night a local model builds topic dossiers. With this on, a cloud model makes a second pass and edits them itself: it spots superseded decisions, expired deadlines, contradictions between sources. Off — it only writes a report and you apply the fixes. Transcripts, minutes and the “Author edits” section are never touched; every edit is backed up first.",
                         "夜间由本地模型生成主题档案。开启后，云端模型会进行第二遍并自行修改：发现被推翻的决定、已过期的期限、来源之间的矛盾。关闭时只写报告，由你来修改。会议记录、纪要和“作者修改”小节永不触碰；每次修改前先备份。"))
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
                        .charoite(.regular, .s)
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
                Toggle(
                    L.t("Запускать Charoite при входе",
                        "Launch Charoite at login",
                        "登录时启动 Charoite"),
                    isOn: Binding(
                        get: { launchAtLogin.isEnabled },
                        set: { launchAtLogin.setEnabled($0) }))
                if !launchAtLogin.note.isEmpty {
                    Text(launchAtLogin.note)
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
                Toggle(L.t("Бриф и напоминание о записи", "Brief and a nudge to record", "简报与录制提醒"), isOn: $calendarBriefs)
                    .onChange(of: calendarBriefs) { _, on in
                        on
                            ? CalendarService.shared.enable(askForNotifications: true)
                            : CalendarService.shared.disable()
                    }
                Text(L.t("Читает только название и время событий — для кнопки «Бриф», системного уведомления и полосы внутри окна. Запись сама не включается: спрашиваем и ждём ответа, «Не сейчас» по этой встрече больше не повторяем. Локально, ничего не пишет.",
                         "Reads only event titles and times — for the Brief button, a system notification and the in-window bar. Recording never starts on its own: we ask and wait, and “Not now” is remembered for that meeting. Local, write-free.",
                         "仅读取日程的标题与时间——用于「简报」按钮、系统通知和窗口内提示条。录制不会自动开始：我们询问并等待你的选择，选择「暂不」后不再就该会议询问。本地运行，不做任何写入。"))
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
        // Состояние галочки читаем из файла, а не помним своё: конфиг могли
        // поправить руками, и тогда UI обязан показать то, что там лежит.
        .onAppear {
            cloudEditGraph = AppSettings.configFlag("cloud_edit_graph")
            launchAtLogin.refresh()
        }
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
        // Лог ночного прогона — рядом с данными, а не в общем /tmp: туда
        // печатаются вопросы бенча памяти, названия ядер и куски ответов из
        // архива встреч, а /tmp читает любая учётка машины (аудит 16.08).
        let logDir = AppSettings.charoiteRoot.appendingPathComponent("logs")
        FileManager.default.createPrivateDirectory(at: logDir)
        let logPath = logDir.appendingPathComponent("nightly.log").path
        FileManager.default.createPrivateFile(atPath: logPath)

        // Пути — настоящими узлами plist, а не интерполяцией в XML: каталог
        // установки с «&» или «<» в имени иначе ломает или подменяет файл.
        let job: [String: Any] = [
            "Label": "ai.charoite.nightly",
            "ProgramArguments": ["/bin/bash", script.path],
            "StartCalendarInterval": ["Hour": 4, "Minute": 15],
            "StandardOutPath": logPath,
            "StandardErrorPath": logPath,
        ]
        do {
            let data = try PropertyListSerialization.data(
                fromPropertyList: job, format: .xml, options: 0)
            // ~/Library/LaunchAgents — общий каталог всех агентов пользователя,
            // не наш: создать при отсутствии, но права не ужимать (0700 ему
            // ставил createPrivateDirectory — второе мнение по #324, 16.08).
            try? FileManager.default.createDirectory(
                at: nightlyPlistURL.deletingLastPathComponent(),
                withIntermediateDirectories: true)
            try data.write(to: nightlyPlistURL, options: .atomic)
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
        // Демон живёт у корня КОДА (бандл или выбранная папка), а не данных:
        // при коде в приложении проверка по папке данных всегда была красной.
        let daemon = AppSettings.codeRoot.appendingPathComponent("src/daemon.py")
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
                    // Полнота индекса, а не просто «сколько файлов»: индекс
                    // набирается фоном по 48 блоков за поиск, и пока он неполон,
                    // семантика молча работает вполсилы. Без этой строки
                    // человек видит «✓ семантика» и считает, что всё готово.
                    let indexed = await SemanticIndex.shared.count()
                    let chunks = await SemanticIndex.shared.totalChunks()
                    let total = AppSettings.graphDir.map { Self.countGraphFiles($0).onDisk } ?? 0
                    if indexed == 0 {
                        parts.append(L.t("✓ bge-m3 (индекс построится при первом поиске)", "✓ bge-m3 (index builds on first search)", "✓ bge-m3（首次搜索时建立索引）"))
                    } else if total > 0, indexed < total * 9 / 10 {
                        parts.append(L.t("◔ семантика: \(indexed) из \(total) заметок, \(chunks) блоков — индекс ещё набирается",
                                         "◔ semantics: \(indexed) of \(total) notes, \(chunks) chunks — still indexing",
                                         "◔ 语义：\(total) 条中已索引 \(indexed) 条，\(chunks) 个块——仍在建立索引"))
                    } else {
                        parts.append(L.t("✓ семантика: \(indexed) заметок, \(chunks) блоков",
                                         "✓ semantics: \(indexed) notes, \(chunks) chunks",
                                         "✓ 语义：\(indexed) 条笔记，\(chunks) 个块"))
                    }
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
