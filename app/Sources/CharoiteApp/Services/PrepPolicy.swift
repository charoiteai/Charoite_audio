import Foundation

/// Чистые решения экрана подготовки — отдельно от вида, чтобы тестировались.
enum PrepPolicy {
    /// Служебные слова календаря сами по себе не связывают задачу с темой.
    private static let genericEventWords: Set<String> = [
        "встреча", "созвон", "синк", "планерка", "планёрка", "еженедельно",
        "meeting", "call", "sync", "weekly",
        "会议", "同步", "例会",
    ]

    /// Запрос в архив из названия события календаря.
    ///
    /// Календарные названия обрастают служебными хвостами — «(еженедельно)»,
    /// «(перенос)», номера комнат. Для поиска по архиву встреч они шум:
    /// прошлые встречи в графе названы темой, а не регалиями события.
    static func titleQuery(_ title: String) -> String {
        // Не split: у названия целиком в скобках он глотает пустую голову
        // и возвращает хвост со скобкой — а должен быть пустой запрос.
        let cut = title.firstIndex(of: "(").map { String(title[..<$0]) } ?? title
        return cut
            .split(separator: " ", omittingEmptySubsequences: true)
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespaces)
    }

    /// Относится ли встреча или поручение к теме ближайшего события.
    ///
    /// Самая надёжная связь — тот же штамп встречи в пути файла. Для заметок
    /// без штампа используем слова темы в тексте и пути, исключая общие
    /// календарные слова вроде «синк». Двух слов достаточно для длинного
    /// названия; у короткой конкретной темы достаточно одного.
    static func matchesTopic(
        text: String,
        source: String,
        topic: String,
        relatedDays: Set<String> = []
    ) -> Bool {
        let sourceDay = MeetingSearch.dayKey(source)
        if sourceDay.count == 12, relatedDays.contains(sourceDay) { return true }

        let wanted = MeetingSearch.tokens(of: topic)
            .filter { $0.count > 2 && !genericEventWords.contains($0) }
        guard !wanted.isEmpty else { return false }
        let present = MeetingSearch.tokens(of: text + " " + source)
        let matched = wanted.filter { needle in
            present.contains { word in
                word == needle || (word.count >= 5 && needle.count >= 5
                    && word.prefix(5) == needle.prefix(5))
            }
        }.count
        return matched >= min(2, wanted.count)
    }
}
