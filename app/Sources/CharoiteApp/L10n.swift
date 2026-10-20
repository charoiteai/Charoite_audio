import Foundation

/// Локализация интерфейса тем же ключом, что и документы встреч:
/// sufler.language (ru|en|zh) в config.yaml. Не системная локаль — язык
/// продукта: пользователь, пишущий минутки по-английски, видит и панели
/// по-английски. Строки задаются на месте вызова тройкой переводов —
/// greppable, без словаря на сотню придуманных ключей.
enum L {
    static let lang = AppSettings.uiLanguage

    /// Локаль для дат и чисел — от языка продукта, не от системы: под
    /// русским заголовком системный форматтер писал бы «August 3».
    static var locale: Locale { Locale(identifier: lang) }

    static func t(_ ru: String, _ en: String, _ zh: String) -> String {
        t(ru, en, zh, lang: lang)
    }

    /// Язык явным параметром — тестам: все три ветки проверяются в одном
    /// процессе, а не только та, на которой он запущен.
    static func t(_ ru: String, _ en: String, _ zh: String, lang: String) -> String {
        switch lang {
        case "en": return en
        case "zh": return zh
        default: return ru
        }
    }
}
