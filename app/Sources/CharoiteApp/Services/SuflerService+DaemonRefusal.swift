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
}
