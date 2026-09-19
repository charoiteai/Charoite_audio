import Foundation

#if os(macOS)

/// Свёртка здоровья систем для поверхностей, которые видны без окна: иконка
/// меню-бара и строка статуса в меню (№139).
///
/// До неё у каждого сигнала была своя поверхность и свой срок жизни: запись —
/// `SuflerService` (только при встрече), последняя обработка — строка в меню,
/// ночь — только экран «Сегодня» (сервис создавался лениво при его появлении,
/// а `logs/nightly.json` за 19.09 — `slept`, четыре шага пропущены — не читал
/// никто), Ollama — второй зонд `/api/tags` внутри вью с результатом в `@State`.
/// Входной круг (DS + GLM, 19.09) сошёлся на одном механизме: чистая функция
/// рядом с `PipelineHealthPresentation` (там уже живёт «одна политика
/// представления — много поверхностей»), без нового сервиса, протокола, демона
/// и сторожа (вердикт №68). Владельцы фактов — прежние синглтоны, свёртка —
/// только потребитель их состояний, слова — только владельцев.
///
/// Ранг прибит: **красный — только «данные гибнут»** (отказ записи, мёртвый
/// насос) при живой встрече. Ошибка обработки, лежащая Ollama, проспанная
/// ночь — жёлтые: исходник сохранён, час конвейера или ночь потеряны, запись
/// не пострадала. Один красный на две беды (REC и потеря данных) обесценивал бы
/// рефлекс «бросить всё и смотреть запись» (№110; Important DS входного круга).
///
/// Выходной круг 1 (DS + GLM) добавил три правила, и все три — про то, что
/// **поверхность не решает сама**: слова — у владельца (`errorHeadline`,
/// `explanation(for:)`, `title(for:)`), «кто красит эту поверхность» — у
/// `HealthPresentation` (иконка — худший ярус, точка записи — только запись,
/// строка — работа выше проблемы выше готовности), свежесть — у владельца
/// через один планировщик `HealthClock`, а не «когда вью удобно».
enum HealthTier: Int, Comparable {
    case ok = 0
    case degraded = 1
    case critical = 2

    static func < (lhs: HealthTier, rhs: HealthTier) -> Bool { lhs.rawValue < rhs.rawValue }
}

/// Кто сообщил о проблеме. Порядок объявления = ранг показа: сигналы в
/// вердикте отсортированы по нему (`Comparable`), а не порядком `append`
/// (Minor GLM выходного круга).
enum HealthSource: Int, CaseIterable, Comparable {
    case recording
    case processing
    case ollama
    case nightly

    static func < (lhs: HealthSource, rhs: HealthSource) -> Bool { lhs.rawValue < rhs.rawValue }
}

struct HealthSignal: Equatable {
    let source: HealthSource
    let tier: HealthTier
    let text: String
}

struct HealthVerdict: Equatable {
    /// Худший ярус среди сигналов; `.ok` — сигналов нет.
    let tier: HealthTier
    /// Только проблемы, по рангу источника (запись → обработка → Ollama → ночь).
    let signals: [HealthSignal]

    static let allClear = HealthVerdict(tier: .ok, signals: [])

    /// Первая строка для узкого места (строка меню).
    var headline: String? { signals.first?.text }

    /// Ярус одного источника; `.ok`, если он не жаловался.
    func tier(of source: HealthSource) -> HealthTier {
        signals.first { $0.source == source }?.tier ?? .ok
    }
}

enum HealthRollup {
    /// Свёртка по состояниям владельцев. Ничего не читает и не опрашивает —
    /// поэтому таблица «состояние → ярус» проверяется тестом целиком.
    ///
    /// - `recording`: проблема конвейера записи, учитывается ТОЛЬКО при живой
    ///   встрече — вне записи `PipelineHealthMonitor` хранит прошлое (гейт
    ///   `isRunning` в `pipelineStatusText`).
    /// - `processingError`: заголовок ошибки последней обработки словами
    ///   владельца (`MeetingProcessingService.errorHeadline`); nil — ошибки нет.
    /// - `ollama`: состояние рантайма от `OllamaRuntimeService` — только «сервер
    ///   отвечает/не отвечает»; `.unknown` (пробы ещё не было) — не сигнал;
    ///   вставший инференс сюда не доходит и проявляется как ошибка обработки
    ///   по staleness (Critical GLM входного круга).
    /// - `nightly` + `nightlyAgentConfigured`: состояние ночи; `.never` без
    ///   агента — «не настроено», информация на «Сегодня», не проблема для
    ///   иконки; `.never` при прописанном агенте — ночь не отработала ни разу.
    static func rollup(recording: PipelineHealthProblem?, isRecording: Bool,
                       processingError: String?,
                       ollama: OllamaRuntime,
                       nightly: NightlyState, nightlyAgentConfigured: Bool = false) -> HealthVerdict {
        var signals: [HealthSignal] = []
        if isRecording, let recording {
            signals.append(HealthSignal(source: .recording,
                                        tier: recording.isCritical ? .critical : .degraded,
                                        text: PipelineHealthPresentation.text(for: recording)))
        }
        if let processingError, !processingError.isEmpty {
            signals.append(HealthSignal(source: .processing, tier: .degraded, text: processingError))
        }
        if ollama != .running && ollama != .unknown {
            signals.append(HealthSignal(source: .ollama, tier: .degraded,
                                        text: OllamaRuntimeService.explanation(for: ollama)))
        }
        if NightlyStatus.isProblem(nightly, agentConfigured: nightlyAgentConfigured) {
            signals.append(HealthSignal(source: .nightly, tier: .degraded,
                                        text: NightlyStatusService.title(for: nightly)))
        }
        signals.sort { $0.source < $1.source }
        let tier = signals.map(\.tier).max() ?? .ok
        return HealthVerdict(tier: tier, signals: signals)
    }
}

/// Кто из вердикта красит какую поверхность — одна политика, а не выбор во вью
/// (Important DS и GLM выходного круга: точка записи красилась общим ярусом,
/// живая строка «Обрабатываю…» пряталась за постоянной жалобой).
enum HealthPresentation {
    /// Иконка меню-бара — худший ярус всех источников: это единственная
    /// поверхность, видная без окна и меню.
    static func iconTier(_ verdict: HealthVerdict) -> HealthTier { verdict.tier }

    /// Точка рядом со словом «Запись» — только здоровье записи: чужая беда
    /// (Ollama, ночь) рядом с этим словом обвиняла бы запись.
    static func recordingDotTier(_ verdict: HealthVerdict) -> HealthTier { verdict.tier(of: .recording) }

    /// Подпись иконки для VoiceOver — адресная: первая проблема словами владельца.
    static func iconLabel(_ verdict: HealthVerdict) -> String? { verdict.headline }

    /// Строка меню: идёт работа → проблема → терминальное состояние → покой.
    /// «Проблема выше готовности» писалось про терминальное «Встреча готова»,
    /// а не про живое «Обрабатываю…» — у него другого места в меню нет.
    static func menuLine(_ verdict: HealthVerdict, isRecording: Bool, isProcessing: Bool,
                         processingText: String?, hasReadyMeeting: Bool) -> (text: String, tier: HealthTier?) {
        if isRecording {
            return (L.t("Запись", "Recording", "录音中") + " ·", recordingDotTier(verdict))
        }
        if isProcessing {
            return (processingText ?? L.t("Обрабатываю встречу…", "Processing…", "正在处理…"), nil)
        }
        if let headline = verdict.headline {
            return (headline, verdict.tier)
        }
        if hasReadyMeeting {
            return (L.t("Встреча готова", "Meeting ready", "会议已就绪"), .ok)
        }
        return (L.t("Готов к записи", "Ready to record", "可以录音"), .ok)
    }
}

extension NightlyStatus {
    /// Что из состояний ночи — проблема для иконки. `.never` без агента — не
    /// настроено (свежая установка): вечно жёлтая иконка приучила бы её не
    /// читать; строка на «Сегодня» об этом и так говорит. `.never` при живом
    /// агенте — ночь не отработала ни разу (Important DS). `.running`/`.ok` — норма.
    static func isProblem(_ state: NightlyState, agentConfigured: Bool) -> Bool {
        switch state {
        case .ok, .running: return false
        case .never: return agentConfigured
        case .failed, .slept, .interrupted, .stale, .foreignScript: return true
        }
    }
}

/// Один планировщик свежести владельцев на приложение: первое чтение — после
/// старта, не в рендере иконки (диск и LaunchAgents на главном потоке — долг
/// №267), дальше — по одному интервалу для всех. Владелец отвечает за факт и
/// умеет его освежить (`refresh()`), но не заводит свой таймер: два сигнала
/// с разной каденцией на одной иконке (ночь — раз в час, Ollama — никогда вне
/// меню) — ровно Critical выходного круга. Не сторож (№68): ничего не
/// перезапускает, читает килобайт JSON и пробует локальный порт.
@MainActor
enum HealthClock {
    static let interval: TimeInterval = 10 * 60
    private static var task: Task<Void, Never>?

    static func start() {
        guard task == nil else { return }
        task = Task { @MainActor in
            while !Task.isCancelled {
                await tick()
                try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
            }
        }
    }

    static func stop() {
        task?.cancel()
        task = nil
    }

    static func tick() async {
        NightlyStatusService.shared.refresh()
        await OllamaRuntimeService.shared.refresh()
    }
}

#endif
