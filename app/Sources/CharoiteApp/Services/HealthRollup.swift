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
/// только потребитель их состояний.
///
/// Ранг прибит: **красный — только «данные гибнут»** (отказ записи, мёртвый
/// насос) при живой встрече. Ошибка обработки, лежащая Ollama, проспанная
/// ночь — жёлтые: исходник сохранён, час конвейера или ночь потеряны, запись
/// не пострадала. Один красный на две беды (REC и потеря данных) обесценивал бы
/// рефлекс «бросить всё и смотреть запись» (№110; Important DS входного круга).
enum HealthTier: Int, Comparable {
    case ok = 0
    case degraded = 1
    case critical = 2

    static func < (lhs: HealthTier, rhs: HealthTier) -> Bool { lhs.rawValue < rhs.rawValue }
}

/// Кто сообщил о проблеме — и в каком порядке они показываются, когда их
/// несколько. Порядок объявления = ранг.
enum HealthSource: String, CaseIterable, Equatable {
    case recording
    case processing
    case ollama
    case nightly
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
}

enum HealthRollup {
    /// Свёртка по состояниям владельцев. Ничего не читает и не опрашивает —
    /// поэтому таблица «состояние → ярус» проверяется тестом целиком.
    ///
    /// - `recording`: проблема конвейера записи, учитывается ТОЛЬКО при живой
    ///   встрече — вне записи `PipelineHealthMonitor` хранит прошлое (гейт
    ///   `isRunning` в `pipelineStatusText`).
    /// - `processingError`: последняя встреча не обработана, исходник сохранён.
    /// - `ollama`: состояние рантайма от `OllamaRuntimeService` — только «сервер
    ///   отвечает/не отвечает»; вставший инференс сюда не доходит и проявляется
    ///   как ошибка обработки по staleness (Critical GLM входного круга).
    /// - `nightly`: состояние ночи; `.never` — ночь не настроена, это
    ///   информация на «Сегодня», а не проблема для иконки.
    static func rollup(recording: PipelineHealthProblem?, isRecording: Bool,
                       processingError: Bool,
                       ollama: OllamaRuntime,
                       nightly: NightlyState) -> HealthVerdict {
        var signals: [HealthSignal] = []
        if isRecording, let recording {
            signals.append(HealthSignal(source: .recording,
                                        tier: recording.isCritical ? .critical : .degraded,
                                        text: PipelineHealthPresentation.text(for: recording)))
        }
        if processingError {
            signals.append(HealthSignal(source: .processing, tier: .degraded,
                                        text: L.t("Ошибка обработки — исходник сохранён",
                                                  "Processing failed — source kept",
                                                  "处理失败——原始文件已保留")))
        }
        if ollama != .running {
            signals.append(HealthSignal(source: .ollama, tier: .degraded,
                                        text: OllamaRuntimeService.explanation(for: ollama)))
        }
        if NightlyStatus.isProblem(nightly) {
            signals.append(HealthSignal(source: .nightly, tier: .degraded,
                                        text: NightlyStatusService.title(for: nightly)))
        }
        let tier = signals.map(\.tier).max() ?? .ok
        return HealthVerdict(tier: tier, signals: signals)
    }
}

extension NightlyStatus {
    /// Что из состояний ночи — проблема для иконки. `.never` — не настроено
    /// (свежая установка без агента): вечно жёлтая иконка приучила бы её не
    /// читать; строка на «Сегодня» об этом и так говорит. `.running`/`.ok` — норма.
    static func isProblem(_ state: NightlyState) -> Bool {
        switch state {
        case .ok, .running, .never: return false
        case .failed, .slept, .interrupted, .stale, .foreignScript: return true
        }
    }
}

#endif
