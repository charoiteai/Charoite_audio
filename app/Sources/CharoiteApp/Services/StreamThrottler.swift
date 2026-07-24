import Foundation

/// Троттлинг обновлений UI при стриминге токенов (~30 fps).
///
/// Без него растущая @Published-строка перерисовывается на КАЖДЫЙ токен = O(n²),
/// main thread 100% CPU, beachball (найдено 17.07 в thinking-режиме через sample:
/// 1298/1298 в SwiftUI updateGraph). Общий для всех стримов: главный чат,
/// LocalChat, Sufler (hint/cloud), MenuBar quick-chat.
actor StreamThrottler {
    private var pendingText = ""
    private var lastUIUpdate = Date.distantPast
    private let throttleInterval: TimeInterval

    init(fps: Double = 30) {
        self.throttleInterval = 1.0 / fps
    }

    /// Добавляет токен; возвращает накопленный текст, если пора обновить UI, иначе nil.
    func append(_ token: String) -> String? {
        pendingText += token
        let now = Date()
        guard now.timeIntervalSince(lastUIUpdate) >= throttleInterval else { return nil }
        lastUIUpdate = now
        return pendingText
    }

    func finalText() -> String { pendingText }

    func reset() { pendingText = ""; lastUIUpdate = .distantPast }
}
