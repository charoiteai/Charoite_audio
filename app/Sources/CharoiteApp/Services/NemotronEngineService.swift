import Combine
import Foundation

#if os(macOS)

/// Единый приёмник действий кнопки Nemotron (№622).
@MainActor
final class NemotronEngineService: ObservableObject {
    static let shared = NemotronEngineService()

    /// Что делает кнопка. План (`plan`) эту переменную не меняет никогда.
    enum Action: Equatable {
        case idle
        case running(NemotronActionKind, lastLine: String)
        case cancelling(afterCommit: Bool)
        case outcome(ActionOutcome)

        /// Идёт процесс: «Отмена», занятость и выход из приложения читают это.
        var isActive: Bool {
            switch self {
            case .running, .cancelling: return true
            case .idle, .outcome: return false
            }
        }
    }

    /// Итог действия поверх кнопок нового плана.
    struct ActionOutcome: Equatable {
        var text: String
        var success: Bool
        var offerEnable = false
        var offerReinstall = false
    }

    @Published var action: Action = .idle

    var isActive: Bool { action.isActive }
}

#endif
