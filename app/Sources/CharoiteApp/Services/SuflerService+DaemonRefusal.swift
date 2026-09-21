import Foundation

extension SuflerService {
    /// Демон назвал отказ сам — показываем ЕГО текст и не трогаем попытки.
    ///
    /// «Корень данных не назван» повтором не лечится: окружение процесса от
    /// трёх запусков не изменится, а рецепт, который демон уже прислал,
    /// затирался бы нашим «Запись прервалась — восстанавливаю» (`fail`
    /// присваивает статус, а не добавляет слой). Ветка живёт отдельным
    /// файлом, потому что её место — рядом с решением о перезапуске, а не в
    /// тысячестрочном теле сервиса (круг 3 по коду №332, DS C1 + гейт длины
    /// файла в SwiftLint).
    func giveUpOnNamedRefusal() {
        daemonFatalReason = nil
        captureLossReason = nil      // причина потери захвата к этому отказу не относится
        endSleepGuard()
        statusIsError = true
        preservedFailure = status    // текст демона с рецептом, а не наш «нажмите ещё раз»
        guard let token = lifecycleGate.beginStop() else { return }
        cleanupDisposition = .preserveFailure
        publishLifecycle()
        beginCaptureShutdown(token: token)
    }

    /// Три попытки не помогли — говорим вслух и перестаём крутить.
    ///
    /// Человек уверен, что встреча пишется, а запись давно встала; страж сна
    /// снимаем, иначе провалившаяся запись навсегда запрещала бы маку спать.
    /// Причина потери захвата, если она была, попадает в текст — по ней
    /// понятно, чинить звук или перезапускать.
    func giveUpAfterAttempts() {
        let потеря = captureLossReason
        captureLossReason = nil
        endSleepGuard()
        if let потеря {
            fail(L.t("⛔️ Захват звука потерян (\(потеря)) и не восстановился. Нажмите «Слушать встречу» ещё раз",
                     "⛔️ Audio capture lost (\(потеря)) and did not recover. Press \u{201C}Listen to the meeting\u{201D} again",
                     "⛔️ 音频捕获已丢失（\(потеря)）且未能恢复。请再次点击「旁听会议」"))
        } else {
            fail(L.t("⛔️ Запись остановилась и не восстановилась. Нажмите «Слушать встречу» ещё раз",
                     "⛔️ Recording stopped and did not recover. Press \u{201C}Listen to the meeting\u{201D} again",
                     "⛔️ 录音已停止且未能恢复。请再次点击「旁听会议」"))
        }
        // .preserveFailure без текста: запоздавший статус демона затирал
        // причину (аудит 13.09, DS M1)
        preservedFailure = status
        guard let token = lifecycleGate.beginStop() else { return }
        cleanupDisposition = .preserveFailure
        publishLifecycle()
        beginCaptureShutdown(token: token)
    }
}
