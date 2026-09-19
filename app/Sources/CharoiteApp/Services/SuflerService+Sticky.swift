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
                                            StickyTopic.notifications: 2]

    /// Серьёзность темы: слои-проблемы («собеседников не будет», «права на
    /// микрофон нет») стоят выше живой строки и красятся красным; слои-справки
    /// (уведомления выключены) едут хвостом за живой строкой и не красят её.
    /// Одна политика «слой есть → красный и выше статуса» прятала бы живую
    /// строку всю встречу ради справки (Important DS выходного круга по №310).
    /// Неизвестная тема — проблема: писатель липкого предупреждения без своей
    /// строки здесь считается серьёзным, пока не сказано иное.
    static let stickyInfoTopics: Set<String> = [StickyTopic.notifications]

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

    enum StickyTopic {
        static let channelLoss = "channel_loss"      // тема демона по умолчанию (демон 0.82 без topic)
        static let capture = "capture"
        static let notifications = "notifications"
    }
}
