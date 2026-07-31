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
    var problems: Int { checks.count { $0.state == .blocked } }
    var warnings: Int { checks.count { $0.state == .warning } }
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

    func refresh() {
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
        let required = [
            (".venv/bin/python", root.appendingPathComponent(".venv/bin/python")),
            ("src/daemon.py", root.appendingPathComponent("src/daemon.py")),
            ("config/config.yaml", root.appendingPathComponent("config/config.yaml")),
        ]
        let missing = required.compactMap { label, url in
            fm.fileExists(atPath: url.path) ? nil : label
        }
        let configURL = root.appendingPathComponent("config/config.yaml")
        let configText = try? String(contentsOf: configURL, encoding: .utf8)
        let python = root.appendingPathComponent(".venv/bin/python")
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
        required = (("audio", "device"), ("audio", "samplerate"),
                    ("stt", "backend"), ("llm", "model"), ("sufler", "language"))
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
        if !local.rootExists || !local.missingFiles.isEmpty {
            let missing = local.missingFiles.joined(separator: ", ")
            checks.append(SetupCheck(
                id: "installation",
                state: .blocked,
                title: L.t("Установка не готова", "Installation is not ready", "安装尚未就绪"),
                detail: L.t("Проверьте папку в Настройках. Не найдено: \(missing.isEmpty ? root.path : missing)",
                            "Check the folder in Settings. Missing: \(missing.isEmpty ? root.path : missing)",
                            "请在设置中检查文件夹。缺少：\(missing.isEmpty ? root.path : missing)")))
        } else {
            checks.append(SetupCheck(
                id: "installation",
                state: .ready,
                title: L.t("Приложение и конфиг", "App and configuration", "应用与配置"),
                detail: root.path))
        }

        if let error = local.pythonError {
            checks.append(SetupCheck(
                id: "python",
                state: .blocked,
                title: L.t("Python-контур не запускается", "Python runtime cannot start", "Python 运行环境无法启动"),
                detail: error == "timeout"
                    ? L.t("Проверка не ответила за 15 секунд", "The check did not respond within 15 seconds", "检查在 15 秒内未响应")
                    : L.t("Переустановите зависимости в .venv", "Reinstall the dependencies in .venv", "请重新安装 .venv 中的依赖")))
        } else if !local.pythonMissingModules.isEmpty {
            checks.append(SetupCheck(
                id: "python",
                state: .blocked,
                title: L.t("Не хватает Python-зависимостей", "Python dependencies are missing", "缺少 Python 依赖"),
                detail: ".venv/bin/pip install -r requirements.txt  ·  "
                    + local.pythonMissingModules.joined(separator: ", ")))
        } else {
            checks.append(SetupCheck(
                id: "python",
                state: .ready,
                title: L.t("Python-контур", "Python runtime", "Python 运行环境"),
                detail: L.t("Демон и зависимости запускаются", "Daemon and dependencies can start", "守护进程与依赖可以启动")))
        }

        if let configError = local.configError {
            checks.append(SetupCheck(
                id: "config",
                state: .blocked,
                title: L.t("Конфиг не готов", "Configuration is not ready", "配置尚未就绪"),
                detail: L.t("Исправьте config/config.yaml: \(configError)",
                            "Fix config/config.yaml: \(configError)",
                            "请修复 config/config.yaml：\(configError)")))
        } else if local.configText.flatMap({ AppSettings.parseValue("user_name", in: $0) }) == nil {
            checks.append(SetupCheck(
                id: "identity",
                state: .warning,
                title: L.t("Имя владельца не задано", "Your name is not set", "尚未设置您的姓名"),
                detail: L.t("Заполните sufler.user_name, чтобы отличать себя от собеседников",
                            "Set sufler.user_name so Charoite can distinguish you from other speakers",
                            "请填写 sufler.user_name，以便区分您与其他发言者")))
        }

        switch microphone {
        case .denied, .restricted:
            checks.append(SetupCheck(
                id: "microphone",
                state: .blocked,
                title: L.t("Микрофон запрещён", "Microphone is blocked", "麦克风已被禁用"),
                detail: L.t("Разрешите доступ в Системных настройках › Конфиденциальность › Микрофон",
                            "Allow access in System Settings › Privacy › Microphone",
                            "请在系统设置 › 隐私与安全性 › 麦克风中允许访问")))
        case .notDetermined:
            checks.append(SetupCheck(
                id: "microphone",
                state: .warning,
                title: L.t("Микрофон ещё не разрешён", "Microphone access has not been requested", "尚未请求麦克风权限"),
                detail: L.t("macOS спросит после явного нажатия «Начать»",
                            "macOS will ask after you explicitly press Start",
                            "在您明确点击“开始”后，macOS 会询问")))
        default:
            checks.append(SetupCheck(
                id: "microphone",
                state: .ready,
                title: L.t("Микрофон", "Microphone", "麦克风"),
                detail: L.t("Доступ разрешён", "Access allowed", "已允许访问")))
        }

        let deviceMode = local.configText.flatMap { AppSettings.parseValue("device", in: $0) } ?? "auto"
        if local.pythonError == nil, local.pythonMissingModules.isEmpty {
            if let audioError = local.audioError {
                checks.append(SetupCheck(
                    id: "audio",
                    state: .blocked,
                    title: L.t("Аудиоустройства недоступны", "Audio devices are unavailable", "音频设备不可用"),
                    detail: audioError))
            } else if local.inputDevices.isEmpty {
                checks.append(SetupCheck(
                    id: "audio",
                    state: .blocked,
                    title: L.t("Нет аудиовхода", "No audio input", "没有音频输入"),
                    detail: L.t("Python не видит ни микрофон, ни виртуальное устройство",
                                "Python sees neither a microphone nor a virtual device",
                                "Python 未检测到麦克风或虚拟设备")))
            } else if deviceMode != "blackhole",
                      !SetupReadinessPolicy.hasMicrophoneInput(local.inputDevices) {
                checks.append(SetupCheck(
                    id: "audio",
                    state: .blocked,
                    title: L.t("Микрофон не найден", "No microphone was found", "未找到麦克风"),
                    detail: L.t("Виден только виртуальный аудиовход; ваш голос не запишется",
                                "Only a virtual input is visible; your voice will not be recorded",
                                "仅检测到虚拟音频输入；您的声音不会被录制")))
            } else if deviceMode != "mic", !SetupReadinessPolicy.hasSystemAudioInput(local.inputDevices) {
                checks.append(SetupCheck(
                    id: "audio",
                    state: .warning,
                    title: L.t("Только очные встречи", "In-person meetings only", "仅支持线下会议"),
                    detail: L.t("Микрофон найден, BlackHole — нет: звук Zoom/Meet не попадёт в запись",
                                "A microphone was found, but not BlackHole: Zoom/Meet audio will not be recorded",
                                "已找到麦克风，但未找到 BlackHole：Zoom/Meet 的声音不会被录制")))
            } else {
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
                checks.append(SetupCheck(
                    id: "audio",
                    state: .ready,
                    title: L.t("Источники звука", "Audio sources", "音频来源"),
                    detail: detail))
            }
        }

        let config = local.configText ?? ""
        let mainModel = AppSettings.parseValue("model", in: config) ?? ""
        let smallModel = AppSettings.parseValue("small_model", in: config) ?? ""
        switch ollama {
        case .unavailable:
            checks.append(SetupCheck(
                id: "ollama",
                state: .blocked,
                title: L.t("Ollama не отвечает", "Ollama is not responding", "Ollama 无响应"),
                detail: L.t("Запустите Ollama или проверьте адрес в Настройках",
                            "Start Ollama or check its address in Settings",
                            "请启动 Ollama，或在设置中检查其地址")))
        case .available(let installed):
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
        }

        if let rawGraph = AppSettings.parseValue("graph_dir", in: config), !rawGraph.isEmpty {
            let graph = AppSettings.resolvePath(rawGraph, relativeTo: root)
            var isDirectory: ObjCBool = false
            if FileManager.default.fileExists(atPath: graph.path, isDirectory: &isDirectory),
               isDirectory.boolValue {
                checks.append(SetupCheck(
                    id: "graph",
                    state: .ready,
                    title: L.t("Граф встреч", "Meeting graph", "会议图谱"),
                    detail: graph.path))
            } else {
                checks.append(SetupCheck(
                    id: "graph",
                    state: .blocked,
                    title: L.t("Папка графа не существует", "The graph folder does not exist", "图谱文件夹不存在"),
                    detail: graph.path))
            }
        } else {
            checks.append(SetupCheck(
                id: "graph",
                state: .warning,
                title: L.t("Граф выключен", "The graph is off", "图谱已关闭"),
                detail: L.t("Стенограмма сохранится, но память встреч не построится",
                            "The transcript will be saved, but meeting memory will not be built",
                            "逐字稿会保存，但不会建立会议记忆")))
        }

        return SetupReadinessSnapshot(checks: checks)
    }
}

#endif
