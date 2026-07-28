import Foundation

/// Локализация iPhone-компаньона. Тот же приём, что на Mac: тройка переводов
/// на месте вызова, без словаря придуманных ключей.
///
/// Отличие от macOS — источник языка. Там его задаёт `sufler.language` из
/// config.yaml, здесь конфига нет: телефон не знает про папку установки.
/// Берём системную локаль и позволяем переопределить вручную — человек,
/// ведущий встречи по-английски на русском телефоне, выберет язык сам.
enum L {
    private static let key = "ui.language"

    /// ru | en | zh. Пусто — значит по системной локали.
    static var override: String {
        get { UserDefaults.standard.string(forKey: key) ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: key) }
    }

    static var lang: String {
        if ["ru", "en", "zh"].contains(override) { return override }
        let code = Locale.preferredLanguages.first?.prefix(2).lowercased() ?? "en"
        switch code {
        case "ru": return "ru"
        case "zh": return "zh"
        default: return "en"
        }
    }

    static func t(_ ru: String, _ en: String, _ zh: String) -> String {
        switch lang {
        case "ru": return ru
        case "zh": return zh
        default: return en
        }
    }
}
