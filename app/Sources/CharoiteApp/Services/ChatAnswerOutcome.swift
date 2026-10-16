import Foundation

/// Чем кончился поток ответа. Решается один раз по фактам потока (`done`,
/// отмена, ошибка), а не тем, какая ветка кода сработала: пометка «ответ
/// прерван», строка происхождения и судьба пустого пузыря выводятся отсюда.
enum ChatAnswerOutcome: Equatable {
    /// Пришёл `done`: ответ полный, даже если «Стоп» нажали после него.
    case complete
    /// «Стоп» до `done`.
    case stopped
    /// Поток кончился без `done` и без ошибки (соединение закрыли молча).
    case dropped
    /// Ошибка транспорта или HTTP.
    case failed

    static func decide(sawDone: Bool, cancelled: Bool, failed: Bool) -> ChatAnswerOutcome {
        if sawDone { return .complete }
        if cancelled { return .stopped }
        return failed ? .failed : .dropped
    }

    /// Непустой ответ без `done` — неполный, чем бы он ни кончился.
    func marksInterrupted(textIsEmpty: Bool) -> Bool { self != .complete && !textIsEmpty }

    /// Строка происхождения и чипы описывают дошедший текст: у полного ответа
    /// всегда, у молча оборванного — только если что-то дошло.
    func getsMeta(textIsEmpty: Bool) -> Bool {
        self == .complete || (self == .dropped && !textIsEmpty)
    }
}
