import Foundation

/// Какое действие кнопки Nemotron шло.
enum NemotronActionKind: Equatable {
    /// «Поставить и включить» — установщик.
    case install
    /// «Включить» — проба `--check` перед записью ключа.
    case check
}

/// Решение после действия кнопки: писать ли `sufler.diarize_backend: nemotron`
/// и что показать человеку. Чистые функции — под табличный тест (№622).
enum NemotronDecision: Equatable {
    /// Все условия выполнены — условная запись ключа ещё не делалась.
    case attemptWrite
    /// Ключ записан.
    case written(String)
    /// Ключ не пишется; `offerEnable` — показать «Включить», `offerReinstall` —
    /// «Переставить» (после неудачной пробы, если план разрешает установку).
    case stop(String, offerEnable: Bool, offerReinstall: Bool)

    static var cancelledText: String { L.t("отменено", "cancelled", "已取消") }

    /// Исход процесса и проверки ключа → решение. Порядок: исход, корень,
    /// сырое значение ключа, `offer.enable.allowed` нового плана, результат
    /// условной записи (`cas == nil` — ещё не писали).
    static func decide(outcome: LongProcessOutcome,
                       kind: NemotronActionKind,
                       rootChanged: Bool,
                       r0: String?,
                       r1: String?,
                       enableAllowed: Bool,
                       enableReason: String,
                       cas: AppSettings.ConfigCAS?) -> NemotronDecision {
        if let failure = outcomeFailure(outcome, kind: kind) { return failure }
        if rootChanged {
            return .stop(L.t("папку данных сменили, пока шло действие — ключ не записан",
                             "the data folder changed while this was running — the key was not written",
                             "运行期间数据文件夹已更改——未写入配置键"),
                         offerEnable: false, offerReinstall: false)
        }
        let changed = L.t("конфиг изменили, пока шло действие — движок поставлен, но не включён",
                          "the config changed while this was running — the engine is installed but not enabled",
                          "运行期间配置已被修改——引擎已安装但未启用")
        if r1 != r0 { return .stop(changed, offerEnable: true, offerReinstall: false) }
        if !enableAllowed { return .stop(enableReason, offerEnable: false, offerReinstall: false) }
        switch cas {
        case nil:
            return .attemptWrite
        case .written:
            return .written(kind == .install
                ? L.t("Nemotron поставлен и включён", "Nemotron is installed and enabled", "Nemotron 已安装并启用")
                : L.t("Nemotron включён", "Nemotron is enabled", "Nemotron 已启用"))
        case .changed:
            return .stop(changed, offerEnable: true, offerReinstall: false)
        case .failed(let reason):
            return .stop(L.t("не удалось записать config.yaml: ", "could not write config.yaml: ",
                             "无法写入 config.yaml：") + reason,
                         offerEnable: false, offerReinstall: false)
        }
    }

    /// Исход процесса, после которого ключ не пишется, — текст; `nil` — код 0
    /// без отмены, идём дальше. Одна функция для установки и пробы.
    static func outcomeFailure(_ o: LongProcessOutcome, kind: NemotronActionKind) -> NemotronDecision? {
        if o.code == 0 && o.reason == .exit && !o.notStarted {
            guard o.cancelRequested else { return nil }
            return .stop(kind == .install
                ? L.t("поставлено, не включено", "installed, not enabled", "已安装，未启用")
                : L.t("проверку отменили — не включено", "the check was cancelled — not enabled", "检查已取消——未启用"),
                offerEnable: true, offerReinstall: false)
        }
        let tail = reason(o)
        if o.cancelRequested {
            let bySignal = o.reason == .uncaughtSignal && (o.code == SIGTERM || o.code == SIGKILL)
            if o.notStarted || o.code == 13 || bySignal {
                return .stop(cancelledText, offerEnable: false, offerReinstall: false)
            }
            if o.code == 1 { return .stop(tail, offerEnable: false, offerReinstall: false) }
        }
        if o.notStarted {
            return .stop(L.t("не удалось запустить: ", "could not start: ", "无法启动：") + o.stderrTail,
                         offerEnable: false, offerReinstall: false)
        }
        if o.reason == .uncaughtSignal {
            return .stop(L.t("прервано сигналом \(o.code)", "interrupted by signal \(o.code)",
                             "被信号 \(o.code) 中断"),
                         offerEnable: false, offerReinstall: false)
        }
        if o.code == 12 {
            let busy = tail.hasPrefix("занято") ? tail : "занято: " + tail
            return .stop(busy, offerEnable: false, offerReinstall: false)
        }
        return .stop(tail.isEmpty ? L.t("код \(o.code)", "exit code \(o.code)", "退出码 \(o.code)") : tail,
                     offerEnable: false, offerReinstall: kind == .check)
    }

    /// Причина отказа: последняя строка stderr, иначе последняя непустая строка
    /// stdout — у `--check` бывает и так, и так.
    static func reason(_ o: LongProcessOutcome) -> String {
        if !o.stderrTail.isEmpty { return o.stderrTail }
        return o.stdout.split(whereSeparator: \.isNewline)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .last { !$0.isEmpty } ?? ""
    }
}
