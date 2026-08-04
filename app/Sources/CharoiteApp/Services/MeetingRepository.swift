import Combine
import Foundation

#if os(macOS)

/// Единое представление встречи для всех экранов приложения.
///
/// Markdown и статусы конвейера остаются источником истины. Repository лишь
/// собирает их в одну модель и кэширует разобранную карточку, чтобы «Сегодня»,
/// библиотека и поиск не перечитывали одни файлы независимо друг от друга.
struct MeetingRecord: Identifiable, Equatable {
    let snapshot: MeetingProcessingSnapshot
    let card: MeetingCard

    var id: String { snapshot.meetingID }
    var title: String { snapshot.title }
    var startedAt: Date { snapshot.startedDate }
    var state: MeetingProcessingSnapshot.State {
        MeetingProcessingPolicy.resolvedState(snapshot)
    }

    var searchableText: String {
        ([title, card.gist ?? ""] + card.participants + card.decisions + card.tasks)
            .joined(separator: "\n")
            .lowercased()
    }
}

@MainActor
final class MeetingRepository: ObservableObject {
    static let shared = MeetingRepository()

    @Published private(set) var records: [MeetingRecord] = []
    private var cache: [String: MeetingRecord] = [:]
    private var cancellables: Set<AnyCancellable> = []

    private init(processing: MeetingProcessingService = .shared) {
        processing.$history
            .receive(on: RunLoop.main)
            .sink { [weak self] snapshots in
                Task { @MainActor in self?.apply(snapshots) }
            }
            .store(in: &cancellables)
        apply(processing.history)
    }

    func record(id: String?) -> MeetingRecord? {
        guard let id else { return nil }
        return records.first { $0.id == id }
    }

    /// Находка архива и статус используют разные имена, но общий цифровой
    /// ключ даты/времени. Благодаря ему результат поиска открывает карточку,
    /// а не отправляет пользователя в сырой Markdown.
    func record(matching hit: MeetingSearch.Hit) -> MeetingRecord? {
        records.first { MeetingSearch.dayKey($0.id) == hit.day }
    }

    private func apply(_ snapshots: [MeetingProcessingSnapshot]) {
        var next: [MeetingRecord] = []
        var nextCache: [String: MeetingRecord] = [:]
        for snapshot in snapshots {
            if let cached = cache[snapshot.meetingID], cached.snapshot == snapshot {
                next.append(cached)
                nextCache[snapshot.meetingID] = cached
                continue
            }
            let card = snapshot.state == .ready
                ? MeetingCardLoader.load(for: snapshot)
                : MeetingCard()
            let record = MeetingRecord(snapshot: snapshot, card: card)
            next.append(record)
            nextCache[snapshot.meetingID] = record
        }
        cache = nextCache
        records = next
    }
}

#endif
