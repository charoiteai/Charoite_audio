import Foundation

/// Чем кончился поток ответа. Решается один раз по фактам потока (`done`,
/// отмена, ошибка), а не тем, какая ветка кода сработала: пометка «ответ
/// прерван», строка происхождения и судьба пустого пузыря выводятся отсюда.
enum ChatAnswerOutcome: Equatable {
    /// Пришёл `done`: ответ полный, даже если «Стоп» нажали после него.
    case complete
    /// Пришёл `done` с `done_reason: "length"`: модель упёрлась в потолок
    /// `num_predict` — текст дошёл, но оборван (DS, круг 4 по #804).
    case truncated
    /// «Стоп» до `done`.
    case stopped
    /// Поток кончился без `done` и без ошибки (соединение закрыли молча).
    case dropped
    /// Ошибка транспорта, HTTP или строка `{"error": …}` посреди потока.
    case failed

    static func decide(sawDone: Bool, doneReason: String? = nil,
                       cancelled: Bool, failed: Bool) -> ChatAnswerOutcome {
        if sawDone { return doneReason == "length" ? .truncated : .complete }
        if cancelled { return .stopped }
        return failed ? .failed : .dropped
    }

    /// Непустой ответ, кроме полного, — неполный, чем бы он ни кончился.
    func marksInterrupted(textIsEmpty: Bool) -> Bool { self != .complete && !textIsEmpty }

    /// Строка происхождения и чипы описывают дошедший текст: у полного ответа
    /// всегда, у молча оборванного — только если что-то дошло.
    func getsMeta(textIsEmpty: Bool) -> Bool {
        self == .complete || ((self == .truncated || self == .dropped) && !textIsEmpty)
    }
}

/// Ollama закончила начатый поток объектом `{"error": …}` (упал раннер, не
/// хватило памяти): это ошибка с причиной, а не молчаливый обрыв.
struct ChatStreamError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}
