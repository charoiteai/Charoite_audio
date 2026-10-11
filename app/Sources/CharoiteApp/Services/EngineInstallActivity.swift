import Combine
import Foundation

#if os(macOS)

/// Общий признак «идёт установка движка разметки голосов»: набор голосов
/// (`ModelPullService`, ключ `diarization`) или Nemotron (установка и проба).
///
/// Установщик Nemotron сам ставит набор голосов, а два загрузчика пишут один
/// `models/diar/*.part` без замка (№643) — пока идёт одна установка, вторая
/// не начинается. Объект склеивает публикации двух служб: вид, подписанный
/// только на свою службу, чужую установку не увидел бы и кнопку не выключил.
@MainActor
final class EngineInstallActivity: ObservableObject {
    static let shared = EngineInstallActivity()

    @Published private(set) var busy = false

    private var subscription: AnyCancellable?

    init(pulls: ModelPullService = .shared, nemotron: NemotronEngineService = .shared) {
        // @Published отдаёт новое значение до записи — `busy` верен сразу после
        // присваивания в службе, а не на следующем ходу цикла.
        subscription = pulls.$progress
            .map { $0[ModelPullService.diarizationKey] != nil }
            .combineLatest(nemotron.$action.map(\.isActive))
            .map { $0 || $1 }
            .removeDuplicates()
            .sink { [weak self] value in self?.busy = value }
    }

    /// Подпись к выключенной кнопке.
    static var busyText: String {
        L.t("идёт другая установка", "another installation is running", "另一项安装正在进行")
    }
}

#endif
