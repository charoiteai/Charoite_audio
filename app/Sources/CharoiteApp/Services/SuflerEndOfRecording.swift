import Foundation

/// Решения вокруг конца записи — отдельно от сервиса, чтобы их можно было
/// проверить тестом без демона, аудио и главного актора.
///
/// Цена ошибки здесь максимальная в обе стороны: не поднять запись после
/// краха — человек уверен, что встреча пишется, а её нет (профиль 20.07);
/// поднять после ШТАТНОГО стопа — новая пустая встреча поверх законченной.
/// Именно так автостоп (тишина или потолок длительности) превратился бы в
/// конвейер пустых записей каждые пять минут, поэтому он и идёт через
/// обычный stop(), выставляющий userStopped.
extension SuflerService {

    /// Статус завершённой записи: ручной Стоп или автостоп с причиной.
    nonisolated static func stoppedStatus(autostopReason: String?) -> String {
        switch autostopReason {
        case "limit":
            return L.t("Остановлена автоматически: потолок длительности",
                       "Stopped automatically: duration ceiling",
                       "已自动停止：已达时长上限")
        case "farewell":
            return L.t("Остановлена автоматически: попрощались",
                       "Stopped automatically: goodbyes said",
                       "已自动停止：互道再见")
        case .some:
            return L.t("Остановлена автоматически: тишина",
                       "Stopped automatically: silence",
                       "已自动停止：静音")
        default:
            return L.t("Остановлен", "Stopped", "已停止")
        }
    }

    /// Что делать со смертью демона: поднимать запись заново или нет.
    ///
    /// Чистая функция, потому что цена ошибки здесь максимальная в обе
    /// стороны. Не поднять после краха — человек уверен, что встреча пишется,
    /// а её нет (профиль 20.07). Поднять после ШТАТНОГО стопа — новая пустая
    /// встреча поверх законченной; именно так автостоп (тишина/потолок)
    /// превратился бы в конвейер пустых записей каждые пять минут, поэтому
    /// автостоп и идёт через обычный stop(), выставляющий userStopped.
    enum RestartDecision { case none, restart, giveUp }

    nonisolated static func restartDecision(wasRecording: Bool, userStopped: Bool,
                                            attempts: Int) -> RestartDecision {
        guard wasRecording, !userStopped else { return .none }
        return attempts < 3 ? .restart : .giveUp
    }
}

extension SuflerService {
    /// Гасит ли обновление нити карточку подсказки. Чистая функция — обе
    /// критические ошибки ревью 16.08 (стирание ручного ответа, ампутация
    /// идущего авто-стрима) прошли бы мимо тестов, живи решение в consume.
    /// Гаснет только завершённый И ПРОЧИТАННЫЙ авто-контент: свежая авто-подсказка живёт
    /// минимум hintCardLifetime — карточка пропадала со следующим обновлением нити, за
    /// полминуты не читалась (владелец, 01.09). Стримы/ручной ответ нить не трогает;
    /// новая подсказка сменяет сразу, крестик работает всегда — старьё не копится.
    nonisolated static let hintCardLifetime: TimeInterval = 180
    nonisolated static func threadClearsHint(isHinting: Bool, isAutoHinting: Bool,
                                             hintIsManual: Bool,
                                             ageSeconds: TimeInterval = .infinity) -> Bool {
        !isHinting && !isAutoHinting && !hintIsManual
            && ageSeconds >= hintCardLifetime
    }
}
