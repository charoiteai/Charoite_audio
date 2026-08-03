import Foundation

/// Чистые решения экрана подготовки — отдельно от вида, чтобы тестировались.
enum PrepPolicy {
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
}
