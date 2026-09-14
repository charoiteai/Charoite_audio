import Foundation

/// Политика ошибок кодека — отдельным файлом: `Recorder.swift` упёрся в потолок
/// SwiftLint (1000 строк). Ошибка кодека посреди встречи: сторож застоя в той же
/// беде ротирует, а делегат останавливал — час разговора после сбоя не писался
/// (аудит 13.09, DS I4 / GLM I3). Политика — статикой, чтобы её держал тест.
extension Recorder {
    /// Ошибок кодека подряд, после которых ротация уже не спасает: пустые куски
    /// плодить незачем — честный стоп.
    nonisolated static let maxEncodeErrors = 3

    nonisolated static func actionAfterEncodeError(consecutive: Int) -> StallAction {
        consecutive >= maxEncodeErrors ? .stop : .rotate
    }
}
