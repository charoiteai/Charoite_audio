import Foundation

/// Чистое ядро выхода из приложения (№622, правка 6): запись встречи и
/// установка Nemotron — набор ожиданий, после которого ровно один ответ
/// `NSApp.reply(toApplicationShouldTerminate:)`.
///
/// Флаг `inProgress` ставится только вместе с `.later`: поставленный на входе,
/// после «остаться» он держался бы, и второй Cmd-Q вернул бы `.terminateLater`
/// без диалога и без ответа — приложение не вышло бы никогда.
struct TerminationCoordinator: Equatable {
    enum Wait: Hashable {
        case sufler
        case install
    }

    enum Decision: Equatable {
        /// Ничего не идёт — выйти сразу.
        case now
        /// Человек выбрал остаться на диалоге записи.
        case cancel
        /// Выход решён, ждём набор работ; потом один ответ.
        case later(Set<Wait>)
        /// Повторный вход, пока ждём: без диалога, без новых таймеров.
        case alreadyInProgress
    }

    private(set) var inProgress = false
    private(set) var pending: Set<Wait> = []
    private var replied = false

    /// `askStay` — диалог записи; зовётся только при идущей записи. Только
    /// установка — без диалога: отмена до фиксации прежнее окружение не трогает,
    /// после фиксации установщик доделывает уборку.
    mutating func begin(recording: Bool, installing: Bool, askStay: () -> Bool) -> Decision {
        if inProgress { return .alreadyInProgress }
        guard recording || installing else { return .now }
        if recording && askStay() {
            inProgress = false
            return .cancel
        }
        var waits: Set<Wait> = []
        if recording { waits.insert(.sufler) }
        if installing { waits.insert(.install) }
        pending = waits
        replied = false
        inProgress = true
        return .later(waits)
    }

    /// Ожидание снято. `true` — пора отвечать; ровно один раз за выход,
    /// повторное снятие и снятие чужого ожидания ответа не дают.
    mutating func finish(_ wait: Wait) -> Bool {
        guard inProgress, !replied, pending.remove(wait) != nil, pending.isEmpty else { return false }
        replied = true
        inProgress = false
        return true
    }
}
