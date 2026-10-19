import Combine
import Foundation

#if os(macOS)

/// Общий признак «идёт установка движка разметки голосов»: набор голосов
/// (`ModelPullService`, ключ `diarization`) или Nemotron (установка и проба).
///
/// Установщик Nemotron сам ставит набор голосов тем же загрузчиком, что и
/// кнопка, — пока идёт одна установка, вторая не начинается. Порчу файла это
/// не держит: её держит замок цели в `get_models.download` (№643), он видит и
/// терминал, и второй экземпляр. Здесь — понятный интерфейс: выключенная кнопка
/// вместо «жду: идёт другая загрузка». Объект склеивает публикации двух служб:
/// вид, подписанный только на свою службу, чужую установку не увидел бы.
@MainActor
final class EngineInstallActivity: ObservableObject {
    static let shared = EngineInstallActivity()

    @Published private(set) var busy = false

    private var subscription: AnyCancellable?

    init(pulls: ModelPullService? = nil, nemotron: NemotronEngineService? = nil) {
        let pulls = pulls ?? .shared
        let nemotron = nemotron ?? .shared
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
    nonisolated static var busyText: String {
        L.t("идёт другая установка", "another installation is running", "另一项安装正在进行")
    }
}

#endif
