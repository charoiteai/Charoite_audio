import Foundation

/// Липкие слои статуса (№310) — вынесены из `SuflerService.swift`: файл упёрся
/// в потолок SwiftLint (1000 строк). Само хранилище `stickyLayers` объявлено
/// в классе (`@Published` живёт только там), здесь — приоритет, серьёзность и
/// чтение слоёв.
extension SuflerService {
    /// Приоритет слоёв на экране: потеря канала выше фолбэка захвата, тот
    /// выше подсказки об уведомлениях; неизвестные темы — после известных.
    /// Явный ранг вместо порядка кейсов: «меньший rawValue выигрывает»
    /// читается как опечатка (Minor DS входного круга).
    static let stickyRank: [String: Int] = [StickyTopic.channelLoss: 0, StickyTopic.capture: 1,
                                            StickyTopic.notifications: 2, StickyTopic.retention: 3,
                                            StickyTopic.power: 4]

    /// Серьёзность темы: слои-проблемы («собеседников не будет», «права на
    /// микрофон нет») стоят выше живой строки и красятся красным; слои-справки
    /// (уведомления выключены) едут хвостом за живой строкой и не красят её.
    /// Одна политика «слой есть → красный и выше статуса» прятала бы живую
    /// строку всю встречу ради справки (Important DS выходного круга по №310).
    /// Неизвестная тема — проблема: писатель липкого предупреждения без своей
    /// строки здесь считается серьёзным, пока не сказано иное.
    /// Срок хранения записей не понят (`retention`) — тоже справка: старые
    /// записи не удаляются, но встрече это не мешает; красный слой прятал бы
    /// живую строку до конца встречи (Sonnet и Haiku, круг 2 по #809).
    /// Режим энергосбережения (`power`, №783) — справка: подсказки медленнее,
    /// запись цела.
    static let stickyInfoTopics: Set<String> = [StickyTopic.notifications, StickyTopic.retention,
                                                StickyTopic.power]

    var orderedLayers: [(key: String, value: String)] {
        stickyLayers.sorted { a, b in
            let ra = Self.stickyRank[a.key] ?? 100, rb = Self.stickyRank[b.key] ?? 100
            return ra != rb ? ra < rb : a.key < b.key
        }
    }

    /// Слои-проблемы одной строкой по приоритету через « · ». Показывать
    /// только верхний означало бы молчать всю встречу о втором факте («ваших
    /// реплик в записи нет» под «собеседников не будет») — критика DS входного
    /// круга по №310.
    var stickyStatus: String? {
        let problems = orderedLayers.filter { !Self.stickyInfoTopics.contains($0.key) }
        return problems.isEmpty ? nil : problems.map(\.value).joined(separator: " · ")
    }

    /// Слои-справки — хвостом к живой строке, не вместо неё.
    var stickyInfo: String? {
        let infos = orderedLayers.filter { Self.stickyInfoTopics.contains($0.key) }
        return infos.isEmpty ? nil : infos.map(\.value).joined(separator: " · ")
    }

    /// Сколько слоёв-проблем живо — вид даёт им больше строк, чем одной
    /// (Important GLM выходного круга: два слоя обрезались двумя строками).
    var stickyProblemCount: Int { orderedLayers.filter { !Self.stickyInfoTopics.contains($0.key) }.count }

    /// Событие слоя-справки (срок хранения, режим питания) — только хвост к
    /// живой строке: живую строку оно не заменяет и не красит, иначе тот же
    /// текст шёл бы дважды и красным (Haiku, круг 3 по #809). Снятие справки
    /// поглощается так же: справка живую строку не писала, и её снятие в общей
    /// ветке затирало бы живую строку и красную ошибку (Sonnet C1 входа №783).
    /// Снятие слоя-проблемы (память) идёт общей веткой, как до №783: его
    /// постановка писала живую строку, и только снятие её возвращает (out2 №783).
    /// true — событие поглощено слоем.
    func takeInfoLayer(_ obj: [String: Any], _ text: String) -> Bool {
        guard let sticky = obj["sticky"] as? Bool, let topic = obj["topic"] as? String,
              Self.stickyInfoTopics.contains(topic) else { return false }
        setSticky(topic, sticky ? text : nil)
        return true
    }

    enum StickyTopic {
        static let channelLoss = "channel_loss"      // тема демона по умолчанию (демон 0.82 без topic)
        static let capture = "capture"
        static let notifications = "notifications"
        static let retention = "retention"           // stt_runtime.TOPIC_RETENTION
        static let power = "power"                   // stt_runtime.TOPIC_POWER
    }
}
