import Foundation

/// Локализация интерфейса тем же ключом, что и документы встреч:
/// sufler.language (ru|en|zh) в config.yaml. Не системная локаль — язык
/// продукта: пользователь, пишущий минутки по-английски, видит и панели
/// по-английски. Строки задаются на месте вызова тройкой переводов —
/// greppable, без словаря на сотню придуманных ключей.
enum L {
    static let lang = AppSettings.uiLanguage

    static func t(_ ru: String, _ en: String, _ zh: String) -> String {
        switch lang {
        case "en": return en
        case "zh": return zh
        default: return ru
        }
    }
}
