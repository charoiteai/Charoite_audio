#if os(macOS)
import Foundation

/// Подмашина остановки записи: что делать дальше, пока встреча закрывается.
///
/// Раньше это состояние было размазано по пяти полям сервиса
/// (`stopFallbackTask`, `captureStartTask`, `captureShutdownToken`,
/// `shutdownWaits`, `cleanupDisposition`) и согласовывалось прозой в
/// комментариях. Аудит 14.08 назвал это главной хрупкостью жизненного цикла:
/// сами переходы корректны, но проверить их можно было только чтением кода,
/// а не тестом.
///
/// Здесь — чистый тип без UI и процессов: события на входе, фаза и действие
/// на выходе. Всё, что связано с Process, таймерами и capture, остаётся в
/// сервисе.
///
/// Главное, что чинится вместе с выносом: у фазы «демон не умирает» не было
/// ВЫХОДНЫХ ДУГ. Приложение переставало опрашивать процесс и оставалось в
/// `stopping` навсегда: повторный Стоп ничего не возобновлял, а выход из
/// программы через 14 секунд оставлял зомби с `daemon.lock`.
enum ShutdownPhase: Equatable, Sendable {
    /// Остановка не идёт.
    case idle
    /// Ждём смерти демона, опрашивая процесс. `waits` — сколько раз ждали.
    case waitingDaemon(waits: Int)
    /// Демон пережил SIGKILL и все ожидания. Частый опрос прекращён, но
    /// состояние НЕ конечное: редкая проверка продолжается, и повторный Стоп
    /// возвращает машину к активному добиванию.
    case stuck
    /// Всё закрыто, можно публиковать idle.
    case done
}

/// Что случилось снаружи.
enum ShutdownEvent: Equatable, Sendable {
    /// Человек нажал Стоп (или сработал автостоп). `daemonAlive` — жив ли
    /// процесс в этот момент.
    case stopRequested(daemonAlive: Bool)
    /// Пришёл terminationHandler.
    case daemonExited
    /// Очередная проверка процесса.
    case pollTick(daemonAlive: Bool)
    /// Сработал запасной таймер: terminationHandler не пришёл вовремя.
    case killTimeout
}

/// Что сервису делать после перехода.
enum ShutdownAction: Equatable, Sendable {
    case nothing
    /// Закрыть capture и проверить процесс.
    case closeCapture
    /// Спросить процесс снова через указанное время.
    case pollAgain(after: TimeInterval)
    /// Сказать человеку, что процесс не отпускает, и перейти на редкий опрос.
    case reportStuck
    /// Добить процесс силой (повторный Стоп по застрявшему демону).
    case forceKill
    /// Остановка завершена: публиковать idle и выполнять cleanup.
    case finish
}

enum ShutdownMachine {
    /// Сколько раз опрашиваем процесс часто (по полсекунды), прежде чем
    /// признать его застрявшим. 30 × 0.5 с = 15 секунд после SIGKILL на 12-й.
    static let maxWaits = 30

    /// Интервал частого опроса.
    static let fastPoll: TimeInterval = 0.5

    /// Интервал редкого опроса в застрявшем состоянии. Смысл именно в том,
    /// чтобы он БЫЛ: демон, зависший в finally, рано или поздно отпускает
    /// ресурсы, и приложение обязано это заметить само, без перезапуска.
    static let slowPoll: TimeInterval = 5.0

    static func next(_ phase: ShutdownPhase, on event: ShutdownEvent,
                     maxWaits: Int = maxWaits) -> (ShutdownPhase, ShutdownAction) {
        switch (phase, event) {

        // --- начало остановки
        case (.idle, .stopRequested(let alive)):
            return alive ? (.waitingDaemon(waits: 0), .closeCapture) : (.done, .finish)

        // Повторный Стоп во время обычного ожидания ничего не меняет: процесс
        // уже добивается по расписанию.
        case (.waitingDaemon, .stopRequested):
            return (phase, .nothing)

        // А вот повторный Стоп по ЗАСТРЯВШЕМУ демону — это просьба человека
        // добить его ещё раз. Раньше она молча игнорировалась.
        case (.stuck, .stopRequested):
            return (.waitingDaemon(waits: 0), .forceKill)

        // --- процесс умер
        case (_, .daemonExited):
            return (.done, .finish)

        case (_, .pollTick(false)):
            return (.done, .finish)

        // --- процесс жив
        case (.waitingDaemon(let waits), .pollTick(true)):
            let next = waits + 1
            if next >= maxWaits {
                return (.stuck, .reportStuck)
            }
            return (.waitingDaemon(waits: next), .pollAgain(after: fastPoll))

        // В застрявшем состоянии продолжаем проверять — редко, но продолжаем.
        case (.stuck, .pollTick(true)):
            return (.stuck, .pollAgain(after: slowPoll))

        // --- запасной таймер
        case (.waitingDaemon, .killTimeout):
            return (phase, .closeCapture)

        case (.stuck, .killTimeout):
            return (phase, .nothing)

        // --- всё остальное состояние не меняет
        case (.idle, _), (.done, _):
            return (phase, .nothing)
        }
    }
}
#endif
