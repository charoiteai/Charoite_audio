import Foundation

/// Единственный источник истины для переходов записи.
///
/// `isRunning` недостаточно: между нажатием Start и запуском демона проходит
/// больше секунды, а штатный Stop ещё до 13 секунд закрывает прошлую встречу.
/// В обоих окнах повторная команда раньше запускала второй async-конвейер.
enum RecordingLifecycle: Equatable, Sendable {
    case idle
    case starting
    case recording
    case stopping

    var isTransitioning: Bool {
        self == .starting || self == .stopping
    }

    var isActive: Bool { self != .idle }
}

/// Token gate не даёт устаревшему async completion изменить новую сессию.
///
/// MainActor-владелец (`SuflerService`) сериализует доступ к структуре; UUID
/// нужен не для синхронизации памяти, а для проверки принадлежности результата
/// конкретной попытке Start/Stop.
struct RecordingLifecycleGate {
    private(set) var state: RecordingLifecycle = .idle
    private(set) var token: UUID?

    mutating func beginStart() -> UUID? {
        guard state == .idle else { return nil }
        let next = UUID()
        state = .starting
        token = next
        return next
    }

    func owns(_ candidate: UUID, in expected: RecordingLifecycle) -> Bool {
        state == expected && token == candidate
    }

    mutating func markRecording(_ candidate: UUID) -> Bool {
        guard owns(candidate, in: .starting) else { return false }
        state = .recording
        return true
    }

    /// Переход в stopping одновременно инвалидирует незавершённый Start.
    mutating func beginStop() -> UUID? {
        guard state == .starting || state == .recording else { return nil }
        let next = UUID()
        state = .stopping
        token = next
        return next
    }

    mutating func finishStop(_ candidate: UUID) -> Bool {
        guard owns(candidate, in: .stopping) else { return false }
        state = .idle
        token = nil
        return true
    }
}
