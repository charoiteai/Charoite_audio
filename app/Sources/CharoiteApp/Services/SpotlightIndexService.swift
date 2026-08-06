import Combine
import CoreSpotlight
import Foundation

#if os(macOS)

/// Встречи — в системном Spotlight: Cmd+Space «созвон по препроду» находит
/// карточку встречи так же, как письмо или документ.
///
/// Почему донат CSSearchableItem, а не альтернативы: NSUserActivity-донат
/// индексирует только просмотренные экраны (встреча, которую не открывали,
/// не найдётся вовсе), а Spotlight App Extension требует отдельного таргета
/// и оправдан для тысяч элементов. У нас сотни встреч и готовая модель
/// MeetingRecord — прямой донат проще и полностью локален: индекс Spotlight
/// не покидает машину, что для стенограмм обязательно.
///
/// Две ловушки, найденные при выборе решения:
/// 1. У CSSearchableItem есть срок годности — по умолчанию элемент молча
///    исчезает из индекса примерно через месяц. Встречам протухать нельзя:
///    ставим expirationDate = distantFuture.
/// 2. Дифф-обновление копит мусор: переименованная или удалённая встреча
///    оставалась бы в индексе навсегда. Домен перезаписывается целиком —
///    на сотнях карточек это единицы миллисекунд в фоне.
@MainActor
final class SpotlightIndexService {
    static let shared = SpotlightIndexService()

    // nonisolated: константу читает и фоновый item(for:) — на Swift 6
    // обращение к @MainActor-статику из nonisolated-контекста — ошибка.
    private nonisolated static let domain = "ai.charoite.meetings"
    private var cancellable: AnyCancellable?
    private var lastFingerprint: Set<String> = []

    private init() {}

    /// Подписка на репозиторий. Debounce, потому что records при старте
    /// приложения меняется каскадом (история → карточки), а перезаписывать
    /// индекс имеет смысл один раз по итогу.
    func enable() {
        cancellable = MeetingRepository.shared.$records
            .debounce(for: .seconds(3), scheduler: RunLoop.main)
            .sink { [weak self] records in self?.reindex(records) }
    }

    private func reindex(_ records: [MeetingRecord]) {
        // Карточки дозревают (state == .ready приходит позже), поэтому
        // сравниваем не только состав, но и наполнение: пустая карточка и
        // готовая дают разные записи индекса.
        let fingerprint = Set(records.map { "\($0.id)|\($0.card.gist ?? "")|\($0.card.participants.count)" })
        guard fingerprint != lastFingerprint else { return }
        lastFingerprint = fingerprint

        let items = records.map { Self.item(for: $0) }
        let index = CSSearchableIndex.default()
        // Полная перезапись домена: удаление + вставка. Ошибки не глотаем —
        // сломанный индекс это молча пропавший поиск, статус нужен хотя бы в логе.
        index.deleteSearchableItems(withDomainIdentifiers: [Self.domain]) { deleteError in
            if let deleteError {
                NSLog("[Spotlight] очистка домена не удалась: %@", deleteError.localizedDescription)
            }
            index.indexSearchableItems(items) { indexError in
                if let indexError {
                    NSLog("[Spotlight] индексация не удалась: %@", indexError.localizedDescription)
                } else {
                    NSLog("[Spotlight] в индексе %d встреч", items.count)
                }
            }
        }
    }

    private nonisolated static func item(for record: MeetingRecord) -> CSSearchableItem {
        let attributes = CSSearchableItemAttributeSet(contentType: .text)
        attributes.title = record.title
        // Суть + решения: то, по чему встречу вспоминают. Ограничение по
        // длине осознанное — Spotlight показывает пару строк, а класть в
        // индекс полную стенограмму значит дублировать её вне архива.
        var description = record.card.gist ?? ""
        if !record.card.decisions.isEmpty {
            description += "\n" + record.card.decisions.prefix(3).joined(separator: " · ")
        }
        attributes.contentDescription = String(description.prefix(400))
        attributes.keywords = record.card.participants + ["встреча", "Чароит", "meeting"]
        attributes.contentCreationDate = record.startedAt
        let item = CSSearchableItem(uniqueIdentifier: record.id,
                                    domainIdentifier: domain,
                                    attributeSet: attributes)
        item.expirationDate = .distantFuture   // ловушка 1: молчаливый месячный TTL
        return item
    }
}

#endif
