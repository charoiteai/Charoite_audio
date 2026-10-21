import Foundation

/// План кнопки Nemotron — разобранный вывод `install_engine.py nemotron --plan`
/// (№622). Только нужные поля; неизвестные игнорируются. Решения о том, что
/// можно, приходят из Python (`offer.*.allowed`), своих правил кнопка не строит.
struct NemotronPlan: Decodable, Equatable {
    struct Permission: Decodable, Equatable {
        var allowed: Bool
        var reason: String?
    }

    struct Offer: Decodable, Equatable {
        var state: String
        var reason: String?
        var install: Permission
        var enable: Permission
        var disable: Permission
        var warnings: [String]
    }

    struct Disk: Decodable, Equatable {
        var freeMb: Double?
        var reclaimableMb: Double?
        var needMb: Double?
        var ok: Bool?
        var reason: String?
    }

    struct Memory: Decodable, Equatable {
        var totalMb: Double?
        var warn: Bool?
        var reason: String?
    }

    struct Sizes: Decodable, Equatable {
        var environment: Double?
        var weights: Double?
        var voiceSet: Double?
    }

    struct License: Decodable, Equatable {
        var name: String?
        var url: String?
    }

    struct Engine: Decodable, Equatable {
        var backend: String?
        var ready: Bool?
    }

    var offer: Offer
    var disk: Disk?
    var memory: Memory?
    var sizesMb: Sizes?
    var license: License?
    var network: [String]?
    var engine: Engine?

    /// Восемь кодов `offer.state` из `docs/ru/DIARIZATION.md` (абзац `offer`).
    static let knownStates = ["not_installed", "broken", "ready_off", "enabled", "enabled_broken",
                              "custom_interpreter", "backend_unknown", "config_error"]

    /// Заголовок состояния; неизвестный код — как есть.
    static func stateTitle(_ state: String) -> String {
        switch state {
        case "not_installed":
            return L.t("Nemotron не поставлен", "Nemotron is not installed", "Nemotron 未安装")
        case "broken":
            return L.t("Nemotron поставлен не целиком", "Nemotron is incomplete", "Nemotron 安装不完整")
        case "ready_off":
            return L.t("Nemotron поставлен, не включён", "Nemotron is installed, not enabled", "Nemotron 已安装，未启用")
        case "enabled":
            return L.t("Nemotron включён", "Nemotron is enabled", "Nemotron 已启用")
        case "enabled_broken":
            return L.t("Nemotron включён, но не готов — разбор идёт на sherpa",
                       "Nemotron is enabled but not ready — sherpa is used",
                       "Nemotron 已启用但未就绪——改用 sherpa")
        case "custom_interpreter":
            return L.t("Nemotron на своём интерпретаторе (sufler.nemotron_python)",
                       "Nemotron uses a custom interpreter (sufler.nemotron_python)",
                       "Nemotron 使用自定义解释器（sufler.nemotron_python）")
        case "backend_unknown":
            return L.t("в diarize_backend неизвестный движок — разбор идёт на sherpa",
                       "unknown engine in diarize_backend — sherpa is used",
                       "diarize_backend 中的引擎未知——改用 sherpa")
        case "config_error":
            return L.t("config.yaml не прочитан", "config.yaml could not be read", "无法读取 config.yaml")
        default:
            return state
        }
    }

    /// Текст предупреждения; неизвестный код — как есть. `memory` — причина из
    /// плана (её же показывает блок согласия).
    func warningText(_ code: String) -> String {
        switch code {
        case "memory":
            return memory?.reason ?? code
        case "voice_set_missing":
            return Self.voiceSetMissingText
        case "engine_outdated":
            return L.t("окружение движка не совпадает с версией продукта — переставьте движок",
                       "the engine environment does not match the app version — reinstall the engine",
                       "引擎环境与应用版本不一致——请重新安装引擎")
        case "live_nemotron":
            return L.t("живой поток во время встречи включён отдельным ключом sufler.live_nemotron, эта кнопка им не управляет",
                       "the live stream during a meeting has its own key, sufler.live_nemotron; this button does not change it",
                       "会议期间的实时流由单独的 sufler.live_nemotron 键控制，此按钮不改变它")
        default:
            return code
        }
    }

    static var voiceSetMissingText: String {
        L.t("выбран Nemotron, а набор голосов неполон — без него откатиться на sherpa некуда",
            "Nemotron is selected but the voice set is incomplete — there is nothing to fall back to",
            "已选择 Nemotron，但声音集不完整——无法回退到 sherpa")
    }

    static let warningCodes = ["memory", "voice_set_missing", "live_nemotron", "engine_outdated"]

    // MARK: разбор

    /// Вывод `--plan` → состояние плана. Строка JSON — последняя непустая
    /// строка stdout; верхний `{"error": …}`, код ≠ 0 и нет `offer` — отказ.
    static func parse(_ o: LongProcessOutcome) -> NemotronPlanState {
        let line = o.stdout.split(whereSeparator: \.isNewline)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .last { !$0.isEmpty } ?? ""
        let data = Data(line.utf8)
        let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        if let error = object?["error"] as? String { return .failed(error) }
        if o.notStarted {
            return .failed(L.t("не удалось запустить: ", "could not start: ", "无法启动：") + o.stderrTail)
        }
        guard o.code == 0, o.reason == .exit else {
            let tail = NemotronDecision.reason(o)
            return .failed(tail.isEmpty ? L.t("проверка движка: код \(o.code)", "engine check: exit code \(o.code)",
                                              "引擎检查：退出码 \(o.code)") : tail)
        }
        guard object != nil else {
            return .failed(L.t("проверка движка вернула не JSON", "the engine check did not return JSON",
                               "引擎检查未返回 JSON"))
        }
        guard object?["offer"] != nil else {
            return .failed(L.t("в ответе проверки движка нет offer", "the engine check returned no offer",
                               "引擎检查结果中没有 offer"))
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        do {
            return .ready(try decoder.decode(NemotronPlan.self, from: data))
        } catch {
            return .failed(L.t("ответ проверки движка не разобран: ", "could not read the engine check: ",
                               "无法解析引擎检查结果：") + String(describing: error))
        }
    }
}

/// Что известно о движке. Переменная независима от `action`: план не меняет
/// действие никогда (сверка in2 №5).
enum NemotronPlanState: Equatable {
    case none
    case planning(previous: NemotronPlan?)
    case failed(String)
    case ready(NemotronPlan)

    /// План, по которому считаются кнопки: готовый или прежний во время проверки.
    var effective: NemotronPlan? {
        switch self {
        case .ready(let p): return p
        case .planning(let p): return p
        case .none, .failed: return nil
        }
    }
}

/// Что видно у кнопки Nemotron. Одна чистая функция для тела вида и для
/// кнопки на «Сегодня» — второй копии условий в видах нет.
struct NemotronVisibleActions: Equatable {
    struct Button: Equatable {
        var title: String
        var enabled: Bool
        /// Подпись под кнопкой: почему выключена.
        var note: String?
    }

    /// Блок согласия над «Поставить и включить»: нажатие = согласие.
    struct Consent: Equatable {
        var sizes: String?
        var need: String?
        var licenseName: String?
        var licenseURL: URL?
        var licenseNote: String
        var switchNote: String
        var memoryNote: String?
        var voiceSetNote: String?
        var network: [String]
    }

    enum Cancel: Equatable { case hidden, enabled, disabled }

    var progress: String?
    var cancel: Cancel = .hidden
    var outcome: NemotronEngineService.ActionOutcome?
    var title: String?
    var detail: String?
    var retry = false
    var consent: Consent?
    var install: Button?
    var enable: Button?
    var disable: Button?
    var reinstall: Button?
    var warnings: [String] = []
    var voiceSetButton = false
    var showOnToday = false

    /// Все подписи и тексты — для тестов на словарь.
    var allTexts: [String] {
        var t: [String] = [progress, title, detail, outcome?.text].compactMap { $0 }
        for b in [install, enable, disable, reinstall].compactMap({ $0 }) { t.append(b.title); if let n = b.note { t.append(n) } }
        if let c = consent {
            t += [c.sizes, c.need, c.licenseName, c.memoryNote, c.voiceSetNote].compactMap { $0 }
            t += [c.licenseNote, c.switchNote] + c.network
        }
        return t + warnings
    }
}

extension NemotronVisibleActions {
    static var recordingText: String {
        L.t("идёт запись встречи", "a meeting is being recorded", "正在录制会议")
    }

    static var installTitle: String {
        L.t("Поставить и включить для разбора встреч", "Install and enable for meeting processing", "安装并启用以处理会议")
    }

    static var enableTitle: String { L.t("Включить", "Enable", "启用") }
    static var disableTitle: String { L.t("Выключить", "Disable", "停用") }
    static var reinstallTitle: String { L.t("Переставить", "Reinstall", "重新安装") }

    static var deferredText: String {
        L.t("проверю после встречи", "will check after the meeting", "会议结束后检查")
    }

    static var planningText: String {
        L.t("проверяю движок разметки…", "checking the diarization engine…", "正在检查说话人分离引擎…")
    }

    /// Состояния, где установка осмысленна, даже если сейчас запрещена.
    private static let installStates: Set<String> = ["not_installed", "broken", "enabled_broken"]
    /// Состояния, где ключ уже `nemotron`: установка ключ не пишет — это переустановка (out2 H I1).
    private static let keyOnStates: Set<String> = ["enabled", "enabled_broken"]

    private static func mb(_ v: Double) -> String {
        "\(Int(v.rounded())) " + L.t("МБ", "MB", "MB")
    }

    static func consent(for plan: NemotronPlan) -> Consent {
        let s = plan.sizesMb
        var parts: [String] = []
        if let v = s?.environment, v > 0 { parts.append(L.t("окружение ", "environment ", "环境 ") + mb(v)) }
        if let v = s?.weights, v > 0 { parts.append(L.t("веса ", "weights ", "权重 ") + mb(v)) }
        if let v = s?.voiceSet, v > 0 { parts.append(L.t("набор голосов ", "voice set ", "声音集 ") + mb(v)) }
        let sizes = parts.isEmpty ? nil
            : L.t("займёт на диске: ", "disk space: ", "占用磁盘：") + parts.joined(separator: ", ")
        var need: String?
        if let n = plan.disk?.needMb, n > 0 {
            let gb = String(format: "%.1f", n / 1024).replacingOccurrences(of: ".", with: ",")
            need = L.t("на время установки нужно свободно ≈ \(gb) ГБ",
                       "free space needed during installation ≈ \(gb) GB",
                       "安装期间需要可用空间 ≈ \(gb) GB")
        }
        let memory = plan.offer.warnings.contains("memory") ? plan.memory?.reason.flatMap { $0.isEmpty ? nil : $0 } : nil
        let voiceNote = (s?.voiceSet ?? 0) > 0
            ? L.t("набор голосов поставится вместе с движком", "the voice set will be installed with the engine",
                  "声音集将与引擎一起安装")
            : nil
        return Consent(
            sizes: sizes,
            need: need,
            licenseName: plan.license?.name,
            licenseURL: plan.license?.url.flatMap(URL.init(string:)),
            licenseNote: L.t("нажимая, вы принимаете условия лицензии весов",
                             "by clicking, you accept the weights licence",
                             "点击即表示您接受权重许可条款"),
            switchNote: L.t("после установки голоса после встречи будет размечать Nemotron; вернуть — «Выключить»",
                            "after installation Nemotron labels voices after the meeting; to undo — “Disable”",
                            "安装后会议结束后的说话人标注由 Nemotron 完成；恢复——“停用”"),
            memoryNote: memory,
            voiceSetNote: voiceNote,
            network: plan.network ?? [])
    }

    /// Кнопки и тексты по паре `(action, plan)`, занятости и записи встречи.
    /// `processRunning` — жив процесс действия: без него «Отмена» выключена.
    /// `refusal` — `startRefusal()` службы: действие не начнётся (например, нет
    /// config.yaml) — кнопки выключены с этой подписью, а не молчат (сверка fix1 п. 7).
    /// Без умолчаний: забытый аргумент — ошибка сборки, а не тихая «Отмена» (п. 12).
    static func make(action: NemotronEngineService.Action,
                     plan: NemotronPlanState,
                     busy: Bool,
                     recording: Bool,
                     processRunning: Bool,
                     refusal: String?) -> NemotronVisibleActions {
        var v = NemotronVisibleActions()
        switch action {
        case .running(let kind, let line):
            v.progress = line.isEmpty
                ? (kind == .install ? L.t("ставлю Nemotron…", "installing Nemotron…", "正在安装 Nemotron…")
                                    : L.t("проверяю движок перед включением…", "checking the engine before enabling…",
                                          "启用前检查引擎…"))
                : line
            v.cancel = processRunning ? .enabled : .disabled
            v.showOnToday = true
            return v
        case .cancelling(let afterCommit):
            v.progress = afterCommit
                ? L.t("движок уже поставлен, доделываю уборку", "the engine is already installed, finishing cleanup",
                      "引擎已安装，正在完成清理")
                : L.t("отменяю…", "cancelling…", "正在取消…")
            v.cancel = .disabled
            v.showOnToday = true
            return v
        case .outcome(let o):
            v.outcome = o
            v.showOnToday = true
        case .idle:
            break
        }

        switch plan {
        case .none:
            if recording { v.detail = deferredText }
            return v
        case .planning(let previous):
            v.progress = planningText
            if case .idle = action, let previous { v.showOnToday = todayPredicate(previous) }
            return v
        case .failed(let text):
            v.detail = text
            v.retry = true
            v.showOnToday = true
            return v
        case .ready(let p):
            fill(&v, plan: p, action: action, busy: busy, recording: recording, refusal: refusal)
            if case .idle = action { v.showOnToday = todayPredicate(p) }
            return v
        }
    }

    private static func fill(_ v: inout NemotronVisibleActions, plan p: NemotronPlan,
                             action: NemotronEngineService.Action, busy: Bool, recording: Bool,
                             refusal: String?) {
        let offer = p.offer
        v.title = stateTitle(offer.state)
        if let r = offer.reason, !r.isEmpty { v.detail = r }
        let configError = offer.state == "config_error"
        // Установка, проба и переустановка ждут конца записи и чужой установки;
        // «Выключить» — только правка конфига, запись ей не мешает.
        let blockNote: String? = busy ? EngineInstallActivity.busyText : (recording ? recordingText : refusal)
        let blocked = busy || recording || configError || refusal != nil

        let enableAllowed = offer.enable.allowed
        var consentShown = false
        // Окружение установщика не совпадает с продуктом (№758): кнопку установки видно и
        // рядом с «Включить», а у включённого движка она — «Переставить»; запрет (диск,
        // машина) — выключенной кнопкой с причиной, а не одним текстом предупреждения.
        let outdated = offer.warnings.contains("engine_outdated")
        let title = outdated && keyOnStates.contains(offer.state) ? reinstallTitle : installTitle
        if offer.install.allowed && (!enableAllowed || outdated) {
            v.install = Button(title: title, enabled: !blocked, note: blockNote)
            consentShown = true
        } else if !offer.install.allowed && (installStates.contains(offer.state) || outdated) {
            v.install = Button(title: title, enabled: false, note: offer.install.reason)
        }
        if enableAllowed {
            v.enable = Button(title: enableTitle, enabled: !blocked, note: blockNote)
        }
        if offer.disable.allowed {
            v.disable = Button(title: disableTitle, enabled: !busy && !configError,
                               note: busy ? EngineInstallActivity.busyText : nil)
        }
        // Одна кнопка на действие установки: при видимой установке вторая не нужна (out2 DS M2).
        if case .outcome(let o) = action, o.offerReinstall, offer.install.allowed, v.install == nil {
            v.reinstall = Button(title: reinstallTitle, enabled: !blocked, note: blockNote)
            consentShown = true
        }
        if consentShown { v.consent = consent(for: p) }

        for code in offer.warnings {
            if code == "memory", v.consent?.memoryNote != nil { continue }
            v.warnings.append(p.warningText(code))
            if code == "voice_set_missing" { v.voiceSetButton = true }
        }
    }

    private static func stateTitle(_ state: String) -> String { NemotronPlan.stateTitle(state) }

    /// Постоянные информационные предупреждения: видны в настройках, кнопку на
    /// «Сегодня» не держат (решение главной по сверке out1 №19).
    static let infoWarnings: Set<String> = ["memory", "live_nemotron"]

    /// Предикат кнопки на «Сегодня» при `action == idle` (v2.1): действие или беда.
    /// `disable` не считается: он разрешён у каждого здорового `enabled`.
    static func todayPredicate(_ p: NemotronPlan) -> Bool {
        let o = p.offer
        if o.install.allowed || o.enable.allowed { return true }
        if o.warnings.contains(where: { !infoWarnings.contains($0) }) { return true }
        if o.state == "enabled_broken" || o.state == "backend_unknown" { return true }
        return !NemotronPlan.knownStates.contains(o.state)
    }
}
