import Foundation

extension SuflerService {
    /// Текст, которым заканчивается неудачная запись, — чистая функция от
    /// решения. `nil` означает «оставить то, что уже показано»: демон сам
    /// назвал причину и прислал рецепт, и наш «нажмите ещё раз» его затрёт.
    ///
    /// Разводка живёт здесь, а не двумя вызовами у switch: пока обработчики
    /// были двумя методами без параметров, перестановка вызовов местами
    /// возвращала дефект круга 3 и не красила ни одного теста (круг 4, DS C1).
    nonisolated static func finalFailureText(for decision: RestartDecision,
                                             captureLoss: String?) -> String? {
        switch decision {
        case .giveUpFatal:
            return nil                      // текст демона уже на экране
        case .giveUp:
            if let captureLoss {
                return L.t("⛔️ Захват звука потерян (\(captureLoss)) и не восстановился. Нажмите «Слушать встречу» ещё раз",
                           "⛔️ Audio capture lost (\(captureLoss)) and did not recover. Press \u{201C}Listen to the meeting\u{201D} again",
                           "⛔️ 音频捕获已丢失（\(captureLoss)）且未能恢复。请再次点击「旁听会议」")
            }
            return L.t("⛔️ Запись остановилась и не восстановилась. Нажмите «Слушать встречу» ещё раз",
                       "⛔️ Recording stopped and did not recover. Press \u{201C}Listen to the meeting\u{201D} again",
                       "⛔️ 录音已停止且未能恢复。请再次点击「旁听会议」")
        case .none, .restart:
            assertionFailure("сдаёмся с решением \(decision) — эти ветки не сдаются")
            return nil
        }
    }

    /// Сдаёмся: один путь на оба исхода `.giveUp`.
    ///
    /// Страж сна снимаем всегда — иначе провалившаяся запись навсегда
    /// запрещала бы маку спать. `preservedFailure` пишется ПОСЛЕ выбора
    /// текста: при названном демоном отказе там остаётся его рецепт, при
    /// исчерпанных попытках — наш текст (`.preserveFailure` без текста:
    /// запоздавший статус демона затирал причину, аудит 13.09, DS M1).
    func giveUp(_ decision: RestartDecision) {
        let потеря = captureLossReason
        captureLossReason = nil
        endSleepGuard()
        if let текст = Self.finalFailureText(for: decision, captureLoss: потеря) {
            fail(текст)
        } else {
            statusIsError = true            // текст демона уже показан, красим его
        }
        preservedFailure = status
        guard let token = gateBeginStop() else { return }
        cleanupDisposition = .preserveFailure
        publishLifecycle()
        beginCaptureShutdown(token: token)
    }
}
