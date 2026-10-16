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

    /// Судьба пузыря, в который не дошло ни знака. Исчерпывающий `switch`:
    /// новый исход не соберётся, пока ему не назначена судьба пустого пузыря.
    /// Пустой пузырь без меты в файл не пишется, поэтому каждый исход, кроме
    /// «Стопа», оставляет на экране то, что переживёт перезапуск.
    func emptyBubble(failure: String?) -> EmptyBubble {
        switch self {
        case .complete:
            return .keep   // строка происхождения — след полного пустого ответа
        case .stopped:
            return .remove // заглушка «…», а не ответ
        case .truncated:
            return .explain(L.t("[ответ не пришёл: модель исчерпала потолок длины ответа, не написав текста]",
                                "[no answer: the model hit the reply length limit before writing any text]",
                                "[未收到回答：模型在写出文本前已达到回复长度上限]"))
        case .dropped:
            return .explain(L.t("[ответ не пришёл: соединение закрыто без ответа — Ollama запущена?]",
                                "[no answer: the connection closed without a reply — is Ollama running?]",
                                "[未收到回答：连接在回复前关闭——Ollama 是否在运行？]"))
        case .failed:
            return .explain(failure ?? L.t("[ошибка без описания]", "[error without a description]", "[无描述的错误]"))
        }
    }

    /// Строка ошибки в пузыре. Отказ модели (`{"error": …}`) прислала живая
    /// Ollama — вопрос «Ollama запущена?» только у ошибок транспорта и HTTP.
    static func failureNote(_ error: Error) -> String {
        if let model = error as? ChatStreamError {
            return L.t("[ошибка модели: \(model.message)]", "[model error: \(model.message)]", "[模型错误：\(model.message)]")
        }
        let reason = error.localizedDescription
        return L.t("[ошибка: \(reason) — Ollama запущена?]", "[error: \(reason) — is Ollama running?]",
                   "[错误：\(reason)——Ollama 是否在运行？]")
    }

    enum EmptyBubble: Equatable {
        case keep
        case remove
        case explain(String)
    }

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

/// Не-200 без причины от Ollama (прокси, чужой сервер, пустое тело): ответил
/// не сервер модели — вопрос «Ollama запущена?» уместен, живость не доказана.
struct ChatHTTPError: LocalizedError {
    let status: Int
    var errorDescription: String? { "HTTP \(status)" }
}
