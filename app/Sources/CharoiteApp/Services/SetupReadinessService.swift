import AVFoundation
import Combine
import Foundation

#if os(macOS)

struct SetupCheck: Identifiable, Equatable, Sendable {
    enum State: Equatable, Sendable {
        case ready
        case warning
        case blocked
    }

    let id: String
    let state: State
    let title: String
    let detail: String
}

struct SetupReadinessSnapshot: Equatable, Sendable {
    let checks: [SetupCheck]

    var canStart: Bool { !checks.contains { $0.state == .blocked } }
    // filter().count, а не count { }: SE-0220 появился в Swift 6, а CI
    // собирает и более старым тулчейном — сборка должна проходить на обоих.
    var problems: Int { checks.filter { $0.state == .blocked }.count }
    var warnings: Int { checks.filter { $0.state == .warning }.count }
}

extension SetupReadinessPolicy {
    /// Модели, которые чинятся одной кнопкой.
    ///
    /// Detail проверок пишет рецепт терминалом: «ollama pull qwen3.6:35b-a3b ·
    /// ollama pull bge-m3». Первый запуск «без терминала» значит, что этот
    /// рецепт исполняет само приложение — имя модели достаём отсюда же, а не
    /// заводим параллельный источник правды.
    static func pullableModels(in detail: String) -> [String] {
        guard let re = try? NSRegularExpression(pattern: #"ollama pull ([^\s·]+)"#) else {
            return []
        }
        let range = NSRange(detail.startIndex..., in: detail)
        return re.matches(in: detail, range: range).compactMap { m in
            Range(m.range(at: 1), in: detail).map { String(detail[$0]) }
        }
    }

    /// Команда для копирования, когда починить кнопкой нельзя.
    ///
    /// pip-установка зависимостей требует терминала — но пусть человек хотя бы
    /// не перепечатывает команду с экрана руками.
    static func copyableCommand(in detail: String) -> String? {
        for part in detail.components(separatedBy: "  ·  ") {
            let cmd = part.trimmingCharacters(in: .whitespaces)
            if cmd.hasPrefix(".venv/") || cmd.contains("pip install") {
                return cmd
            }
        }
        return nil
    }
}

/// Чистая политика готовности — отдельно от файлов, сети и TCC ради тестов.
enum SetupReadinessPolicy {
    static func modelAvailable(_ configured: String, in installed: [String]) -> Bool {
        let wanted = configured.lowercased()
        let wantedBase = wanted.split(separator: ":", maxSplits: 1).first.map(String.init) ?? wanted
        return installed.contains { candidate in
            let current = candidate.lowercased()
            let currentBase = current.split(separator: ":", maxSplits: 1).first.map(String.init) ?? current
            // Явный тег — часть модели: qwen3.5:2b не заменяет qwen3.5:4b.
            // Базовое имя без тега означает «любой установленный тег».
            return current == wanted || (!wanted.contains(":") && currentBase == wantedBase)
        }
    }

    static func missingModels(_ configured: [String], installed: [String]) -> [String] {
        var seen = Set<String>()
        return configured.filter { !$0.isEmpty && seen.insert($0).inserted }
            .filter { !modelAvailable($0, in: installed) }
    }

    static func hasSystemAudioInput(_ names: [String]) -> Bool {
        names.contains { $0.localizedCaseInsensitiveContains("blackhole") }
    }

    static func hasMicrophoneInput(_ names: [String]) -> Bool {
        names.contains { !$0.localizedCaseInsensitiveContains("blackhole") }
    }

    /// Состояние права «Запись экрана и системного звука» для нативного захвата.
    ///
    /// Готовность знала про микрофон и BlackHole, но молчала про это право —
    /// хотя без него ScreenCaptureKit не поднимется и вторая сторона разговора
    /// в запись не попадёт. Человек узнавал об этом после встречи, по пустому
    /// каналу собеседника (аудит 0.46.0, P0-10).
    ///
    /// Отдельный случай — `grantedNeedsRestart`. macOS применяет выданное
    /// право к УЖЕ ЗАПУЩЕННОМУ процессу только после перезапуска приложения:
    /// человек нажимает «Разрешить», видит галочку в системных настройках и
    /// уверен, что всё готово, — а захват продолжает падать до конца сессии.
    /// Об этом не говорил ни SETUP, ни интерфейс.
    enum ScreenCaptureAccess: Equatable {
        case granted
        case grantedNeedsRestart
        case denied
    }

    static func screenCaptureAccess(
        preflight: Bool,
        grantedInThisSession: Bool
    ) -> ScreenCaptureAccess {
        if grantedInThisSession { return .grantedNeedsRestart }
        return preflight ? .granted : .denied
    }
}

private struct LocalSetupProbe: Sendable {
    let rootExists: Bool
    let missingFiles: [String]
    let configText: String?
    let pythonMissingModules: [String]
    let inputDevices: [String]
    let pythonError: String?
    let audioError: String?
    let configError: String?
}

private struct PythonSetupProbe: Decodable {
    let missing: [String]
    let inputs: [String]
    let audioError: String?
    let configError: String?

    enum CodingKeys: String, CodingKey {
        case missing
        case inputs
        case audioError = "audio_error"
        case configError = "config_error"
    }
}

private enum OllamaSetupProbe: Sendable {
    case available([String])
    case unavailable
}

/// Проверяет ровно тот локальный runtime, который затем запускает встречу.
/// Никаких загрузок и автоисправлений: мастер только говорит, что готово и
/// какой один следующий шаг нужен. Проверка идёт вне UI-потока.
@MainActor
final class SetupReadinessService: ObservableObject {
    static let shared = SetupReadinessService()

    @Published private(set) var snapshot: SetupReadinessSnapshot?
    @Published private(set) var isChecking = false

    private var generation = UUID()

    private init() {}

    /// Свежесть, при которой повторная проверка — трата: probe дёргает
    /// Python, аудиоустройства и Ollama, и каждый показ «Сегодня» запускал
    /// бы всё заново, включая время записи (ревью 15.08 ×2).
    private static let freshFor: TimeInterval = 60
    private var lastRefreshAt: Date?

    func refresh(force: Bool = false) {
        if !force {
            if isChecking { return }   // in-flight guard: probe уже идёт
            if let last = lastRefreshAt, snapshot != nil,
               Date().timeIntervalSince(last) < Self.freshFor { return }
        }
        lastRefreshAt = Date()
        let token = UUID()
        generation = token
        isChecking = true
        let root = AppSettings.charoiteRoot
        let ollamaURL = AppSettings.ollamaURL
        let microphone = AVCaptureDevice.authorizationStatus(for: .audio)

        Task { [weak self] in
            async let localTask = Task.detached(priority: .utility) {
                Self.inspectLocalRuntime(root: root)
            }.value
            async let ollamaTask = Self.inspectOllama(baseURL: ollamaURL)
            let (local, ollama) = await (localTask, ollamaTask)
            guard let self, self.generation == token else { return }
            self.snapshot = Self.makeSnapshot(
                root: root,
                local: local,
                ollama: ollama,
                microphone: microphone)
            self.isChecking = false
        }
    }

    private nonisolated static func inspectLocalRuntime(root: URL) -> LocalSetupProbe {
        let fm = FileManager.default
        var isDirectory: ObjCBool = false
        let rootExists = fm.fileExists(atPath: root.path, isDirectory: &isDirectory)
            && isDirectory.boolValue
        // Интерпретатор ищем там же, где его запускает приложение: с
        // вложенным контуром .venv рядом с репозиторием может не быть вовсе,
        // и требовать его значило бы показывать красную ошибку на рабочей
        // установке.
        let python = AppSettings.pythonExecutable
        let required = [
            (AppSettings.pythonIsEmbedded
                ? L.t("python в бандле", "python in the bundle", "捆绑包中的 python")
                : ".venv/bin/python", python),
            ("src/daemon.py", AppSettings.codeRoot(dataRoot: root).appendingPathComponent("src/daemon.py")),
            ("config/config.yaml", root.appendingPathComponent("config/config.yaml")),
        ]
        let missing = required.compactMap { label, url in
            fm.fileExists(atPath: url.path) ? nil : label
        }
        let configURL = root.appendingPathComponent("config/config.yaml")
        let configText = try? String(contentsOf: configURL, encoding: .utf8)
        guard fm.isExecutableFile(atPath: python.path) else {
            return LocalSetupProbe(
                rootExists: rootExists,
                missingFiles: missing,
                configText: configText,
                pythonMissingModules: [],
                inputDevices: [],
                pythonError: "venv",
                audioError: nil,
                configError: nil)
        }

        // Один короткий запуск проверяет те же импорты и PortAudio, которыми
        // пользуется демон. Поиск системного устройства в Swift дал бы другую
        // картину, чем sounddevice внутри Python — проверяем рабочий путь.
        let script = #"""
import importlib, json
missing = []
for name in ("yaml", "requests", "numpy", "sounddevice", "onnx_asr"):
    try:
        importlib.import_module(name)
    except Exception:
        missing.append(name)
inputs = []
audio_error = None
config_error = None
if "yaml" not in missing:
    try:
        import pathlib, yaml
        cfg = yaml.safe_load(pathlib.Path("config/config.yaml").read_text(encoding="utf-8")) or {}
        # Обязательны только ключи, которые демон читает БЕЗ дефолта
        # (audio.py: a["device"], a["samplerate"]; stt.py: s["backend"];
        # graph_updater: cfg["llm"]["model"]). sufler.language сюда не входит:
        # он всюду .get(..., "ru"), и конфиг, работавший месяцами, объявлялся
        # «не готовым» — ложный блокер на живой установке.
        required = (("audio", "device"), ("audio", "samplerate"),
                    ("stt", "backend"), ("llm", "model"))
        absent = [".".join(path) for path in required
                  if not isinstance(cfg.get(path[0]), dict) or cfg[path[0]].get(path[1]) in (None, "")]
        if absent:
            config_error = "missing: " + ", ".join(absent)
    except Exception as exc:
        config_error = f"{type(exc).__name__}: {exc}"
if "sounddevice" not in missing:
    try:
        import sounddevice as sd
        inputs = [str(d["name"]) for d in sd.query_devices() if int(d["max_input_channels"]) > 0]
    except Exception as exc:
        audio_error = f"{type(exc).__name__}: {exc}"
print(json.dumps({"missing": missing, "inputs": inputs, "audio_error": audio_error,
                  "config_error": config_error}, ensure_ascii=False))
"""#
        let process = Process()
        process.executableURL = python
        process.arguments = ["-c", script]
        process.currentDirectoryURL = root
        let output = Pipe()
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        let done = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in done.signal() }
        do {
            try process.run()
        } catch {
            return LocalSetupProbe(
                rootExists: rootExists,
                missingFiles: missing,
                configText: configText,
                pythonMissingModules: [],
                inputDevices: [],
                pythonError: error.localizedDescription,
                audioError: nil,
                configError: nil)
        }
        if done.wait(timeout: .now() + 15) == .timedOut {
            process.terminate()
            return LocalSetupProbe(
                rootExists: rootExists,
                missingFiles: missing,
                configText: configText,
                pythonMissingModules: [],
                inputDevices: [],
                pythonError: "timeout",
                audioError: nil,
                configError: nil)
        }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        guard process.terminationStatus == 0,
              let decoded = try? JSONDecoder().decode(PythonSetupProbe.self, from: data) else {
            return LocalSetupProbe(
                rootExists: rootExists,
                missingFiles: missing,
                configText: configText,
                pythonMissingModules: [],
                inputDevices: [],
                pythonError: "probe",
                audioError: nil,
                configError: nil)
        }
        return LocalSetupProbe(
            rootExists: rootExists,
            missingFiles: missing,
            configText: configText,
            pythonMissingModules: decoded.missing,
            inputDevices: decoded.inputs,
            pythonError: nil,
            audioError: decoded.audioError,
            configError: decoded.configError)
    }

    private static func inspectOllama(baseURL: String) async -> OllamaSetupProbe {
        guard let url = URL(string: baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            + "/api/tags") else { return .unavailable }
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]
        cfg.timeoutIntervalForRequest = 4
        guard let (data, response) = try? await URLSession(configuration: cfg).data(from: url),
              (response as? HTTPURLResponse)?.statusCode == 200,
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return .unavailable
        }
        let names = (json["models"] as? [[String: Any]] ?? [])
            .compactMap { $0["name"] as? String }
        return .available(names)
    }

    private static func makeSnapshot(
        root: URL,
        local: LocalSetupProbe,
        ollama: OllamaSetupProbe,
        microphone: AVAuthorizationStatus
    ) -> SetupReadinessSnapshot {
        var checks: [SetupCheck] = []
        checks.append(installationCheck(root: root, local: local))
        checks.append(pythonCheck(local: local))
        if let configCheck = configCheck(local: local) {
            checks.append(configCheck)
        }
        checks.append(microphoneCheck(microphone))
        if let audioCheck = audioCheck(local: local) {
            checks.append(audioCheck)
        }
        checks.append(screenCaptureCheck())
        checks.append(contentsOf: ollamaChecks(local: local, ollama: ollama))
        checks.append(graphCheck(root: root, local: local))
        return SetupReadinessSnapshot(checks: checks)
    }

    private static func installationCheck(root: URL, local: LocalSetupProbe) -> SetupCheck {
        if !local.rootExists || !local.missingFiles.isEmpty {
            let missing = local.missingFiles.joined(separator: ", ")
            return SetupCheck(
                id: "installation",
                state: .blocked,
                title: L.t("Установка не готова", "Installation is not ready", "安装尚未就绪"),
                detail: L.t("Проверьте папку в Настройках. Не найдено: \(missing.isEmpty ? root.path : missing)",
                            "Check the folder in Settings. Missing: \(missing.isEmpty ? root.path : missing)",
                            "请在设置中检查文件夹。缺少：\(missing.isEmpty ? root.path : missing)"))
        }
        return SetupCheck(
            id: "installation",
            state: .ready,
            title: L.t("Приложение и конфиг", "App and configuration", "应用与配置"),
            detail: root.path)
    }

    private static func pythonCheck(local: LocalSetupProbe) -> SetupCheck {
        if let error = local.pythonError {
            return SetupCheck(
                id: "python",
                state: .blocked,
                title: L.t("Python-контур не запускается", "Python runtime cannot start", "Python 运行环境无法启动"),
                detail: error == "timeout"
                    ? L.t("Проверка не ответила за 15 секунд", "The check did not respond within 15 seconds", "检查在 15 秒内未响应")
                    : L.t("Переустановите зависимости в .venv", "Reinstall the dependencies in .venv", "请重新安装 .venv 中的依赖"))
        }
        if !local.pythonMissingModules.isEmpty {
            return SetupCheck(
                id: "python",
                state: .blocked,
                title: L.t("Не хватает Python-зависимостей", "Python dependencies are missing", "缺少 Python 依赖"),
                detail: ".venv/bin/pip install .  ·  "
                    + local.pythonMissingModules.joined(separator: ", "))
        }
        return SetupCheck(
            id: "python",
            state: .ready,
            title: L.t("Python-контур", "Python runtime", "Python 运行环境"),
            detail: L.t("Демон и зависимости запускаются", "Daemon and dependencies can start", "守护进程与依赖可以启动"))
    }

    private static func configCheck(local: LocalSetupProbe) -> SetupCheck? {
        if let configError = local.configError {
            return SetupCheck(
                id: "config",
                state: .blocked,
                title: L.t("Конфиг не готов", "Configuration is not ready", "配置尚未就绪"),
                detail: L.t("Исправьте config/config.yaml: \(configError)",
                            "Fix config/config.yaml: \(configError)",
                            "请修复 config/config.yaml：\(configError)"))
        }
        if local.configText.flatMap({ AppSettings.parseValue("user_name", in: $0) }) == nil {
            return SetupCheck(
                id: "identity",
                state: .warning,
                title: L.t("Имя владельца не задано", "Your name is not set", "尚未设置您的姓名"),
                detail: L.t("Заполните sufler.user_name, чтобы отличать себя от собеседников",
                            "Set sufler.user_name so Charoite can distinguish you from other speakers",
                            "请填写 sufler.user_name，以便区分您与其他发言者"))
        }
        return nil
    }

    private static func microphoneCheck(_ microphone: AVAuthorizationStatus) -> SetupCheck {
        switch microphone {
        case .denied, .restricted:
            return SetupCheck(
                id: "microphone",
                state: .blocked,
                title: L.t("Микрофон запрещён", "Microphone is blocked", "麦克风已被禁用"),
                detail: L.t("Разрешите доступ в Системных настройках › Конфиденциальность › Микрофон",
                            "Allow access in System Settings › Privacy › Microphone",
                            "请在系统设置 › 隐私与安全性 › 麦克风中允许访问"))
        case .notDetermined:
            return SetupCheck(
                id: "microphone",
                state: .warning,
                title: L.t("Микрофон ещё не разрешён", "Microphone access has not been requested", "尚未请求麦克风权限"),
                detail: L.t("macOS спросит после явного нажатия «Начать»",
                            "macOS will ask after you explicitly press Start",
                            "在您明确点击“开始”后，macOS 会询问"))
        default:
            return SetupCheck(
                id: "microphone",
                state: .ready,
                title: L.t("Микрофон", "Microphone", "麦克风"),
                detail: L.t("Доступ разрешён", "Access allowed", "已允许访问"))
        }
    }

    /// Право на нативный захват системного звука — то, о чём готовность молчала.
    private static func screenCaptureCheck() -> SetupCheck {
        switch SetupReadinessPolicy.screenCaptureAccess(
            preflight: CGPreflightScreenCaptureAccess(),
            grantedInThisSession: SystemAudioCapture.accessGrantedInThisSession
        ) {
        case .granted:
            return SetupCheck(
                id: "screen-capture",
                state: .ready,
                title: L.t("Системный звук", "System audio", "系统音频"),
                detail: L.t("Разрешение «Запись экрана» выдано — звук звонка пишется без BlackHole",
                            "Screen Recording permission granted — call audio is captured without BlackHole",
                            "已授予「屏幕录制」权限 — 无需 BlackHole 即可录制通话声音"))
        case .grantedNeedsRestart:
            return SetupCheck(
                id: "screen-capture",
                state: .warning,
                title: L.t("Перезапустите Чароит", "Restart Charoite", "请重启 Charoite"),
                detail: L.t("Разрешение выдано, но macOS применит его только к новому запуску приложения",
                            "Permission granted, but macOS applies it only to a fresh app launch",
                            "权限已授予，但 macOS 仅对重新启动后的应用生效"))
        case .denied:
            return SetupCheck(
                id: "screen-capture",
                state: .warning,
                title: L.t("Нет разрешения на системный звук", "No system audio permission", "缺少系统音频权限"),
                detail: L.t("Системные настройки › Конфиденциальность › Запись экрана — и перезапустите приложение. Без него вторая сторона звонка пишется только через BlackHole",
                            "System Settings › Privacy › Screen Recording, then restart the app. Without it the far side of a call is captured only via BlackHole",
                            "系统设置 › 隐私与安全性 › 屏幕录制，然后重启应用。否则通话对方的声音只能通过 BlackHole 录制"))
        }
    }

    private static func audioCheck(local: LocalSetupProbe) -> SetupCheck? {
        guard local.pythonError == nil, local.pythonMissingModules.isEmpty else { return nil }
        let deviceMode = local.configText.flatMap { AppSettings.parseValue("device", in: $0) } ?? "auto"
        if let audioError = local.audioError {
            return SetupCheck(
                id: "audio",
                state: .blocked,
                title: L.t("Аудиоустройства недоступны", "Audio devices are unavailable", "音频设备不可用"),
                detail: audioError)
        }
        if local.inputDevices.isEmpty {
            return SetupCheck(
                id: "audio",
                state: .blocked,
                title: L.t("Нет аудиовхода", "No audio input", "没有音频输入"),
                detail: L.t("Python не видит ни микрофон, ни виртуальное устройство",
                            "Python sees neither a microphone nor a virtual device",
                            "Python 未检测到麦克风或虚拟设备"))
        }
        if deviceMode != "blackhole",
           !SetupReadinessPolicy.hasMicrophoneInput(local.inputDevices) {
            return SetupCheck(
                id: "audio",
                state: .blocked,
                title: L.t("Микрофон не найден", "No microphone was found", "未找到麦克风"),
                detail: L.t("Виден только виртуальный аудиовход; ваш голос не запишется",
                            "Only a virtual input is visible; your voice will not be recorded",
                            "仅检测到虚拟音频输入；您的声音不会被录制"))
        }
        if deviceMode != "mic", !SetupReadinessPolicy.hasSystemAudioInput(local.inputDevices) {
            return SetupCheck(
                id: "audio",
                state: .warning,
                title: L.t("Только очные встречи", "In-person meetings only", "仅支持线下会议"),
                detail: L.t("Микрофон найден, BlackHole — нет: звук Zoom/Meet не попадёт в запись",
                            "A microphone was found, but not BlackHole: Zoom/Meet audio will not be recorded",
                            "已找到麦克风，但未找到 BlackHole：Zoom/Meet 的声音不会被录制"))
        }
        let detail: String
        switch deviceMode {
        case "mic":
            detail = L.t("Микрофон выбран в config.yaml",
                         "Microphone selected in config.yaml",
                         "config.yaml 中已选择麦克风")
        case "blackhole":
            detail = L.t("В config.yaml выбран только системный звук",
                         "Only system audio is selected in config.yaml",
                         "config.yaml 中仅选择了系统音频")
        default:
            detail = L.t("Микрофон и системный звук доступны",
                         "Microphone and system audio are available",
                         "麦克风与系统音频均可用")
        }
        return SetupCheck(
            id: "audio",
            state: .ready,
            title: L.t("Источники звука", "Audio sources", "音频来源"),
            detail: detail)
    }

    private static func ollamaChecks(local: LocalSetupProbe, ollama: OllamaSetupProbe) -> [SetupCheck] {
        let config = local.configText ?? ""
        switch ollama {
        case .unavailable:
            return [SetupCheck(
                id: "ollama",
                state: .blocked,
                title: L.t("Ollama не отвечает", "Ollama is not responding", "Ollama 无响应"),
                detail: L.t("Запустите Ollama или проверьте адрес в Настройках",
                            "Start Ollama or check its address in Settings",
                            "请启动 Ollama，或在设置中检查其地址"))]
        case .available(let installed):
            var checks: [SetupCheck] = []
            let mainModel = AppSettings.parseValue("model", in: config) ?? ""
            let smallModel = AppSettings.parseValue("small_model", in: config) ?? ""
            let missing = SetupReadinessPolicy.missingModels(
                [mainModel, smallModel], installed: installed)
            if missing.isEmpty {
                checks.append(SetupCheck(
                    id: "ollama",
                    state: .ready,
                    title: L.t("Локальные модели", "Local models", "本地模型"),
                    detail: L.t("Ollama и обязательные модели готовы",
                                "Ollama and required models are ready",
                                "Ollama 与必需模型已就绪")))
            } else {
                checks.append(SetupCheck(
                    id: "ollama",
                    state: .blocked,
                    title: L.t("Не установлены обязательные модели", "Required models are missing", "缺少必需模型"),
                    detail: missing.map { "ollama pull \($0)" }.joined(separator: "  ·  ")))
            }
            let embed = AppSettings.parseValue("embed_model", in: config) ?? "bge-m3:latest"
            if !SetupReadinessPolicy.modelAvailable(embed, in: installed) {
                checks.append(SetupCheck(
                    id: "semantics",
                    state: .warning,
                    title: L.t("Поиск пока без семантики", "Search is lexical only", "搜索目前仅支持词法匹配"),
                    detail: "ollama pull \(embed)"))
            }
            return checks
        }
    }

    private static func graphCheck(root: URL, local: LocalSetupProbe) -> SetupCheck {
        let config = local.configText ?? ""
        guard let rawGraph = AppSettings.parseValue("graph_dir", in: config), !rawGraph.isEmpty else {
            return SetupCheck(
                id: "graph",
                state: .warning,
                title: L.t("Граф выключен", "The graph is off", "图谱已关闭"),
                detail: L.t("Стенограмма сохранится, но память встреч не построится",
                            "The transcript will be saved, but meeting memory will not be built",
                            "逐字稿会保存，但不会建立会议记忆"))
        }
        let graph = AppSettings.resolvePath(rawGraph, relativeTo: root)
        var isDirectory: ObjCBool = false
        if FileManager.default.fileExists(atPath: graph.path, isDirectory: &isDirectory),
           isDirectory.boolValue {
            return SetupCheck(
                id: "graph",
                state: .ready,
                title: L.t("Граф встреч", "Meeting graph", "会议图谱"),
                detail: graph.path)
        }
        return SetupCheck(
            id: "graph",
            state: .blocked,
            title: L.t("Папка графа не существует", "The graph folder does not exist", "图谱文件夹不存在"),
            detail: graph.path)
    }

}

#endif
