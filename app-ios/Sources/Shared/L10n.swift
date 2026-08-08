import Foundation

/// Локализация iPhone-компаньона. Тот же приём, что на Mac: тройка переводов
/// на месте вызова, без словаря придуманных ключей.
///
/// Отличие от macOS — источник языка. Там его задаёт `sufler.language` из
/// config.yaml, здесь конфига нет: телефон не знает про папку установки.
/// Берём системную локаль и позволяем переопределить вручную — человек,
/// ведущий встречи по-английски на русском телефоне, выберет язык сам.
/// Файл лежит в `Sources/Shared`, потому что его собирают оба таргета:
/// приложение и расширение виджетов. Пока он жил в таргете приложения, Live
/// Activity в Dynamic Island оставалась русской при английском интерфейсе —
/// подписи там были захардкожены за неимением `L`. У расширения свой
/// UserDefaults, поэтому ручной выбор языка ему не виден: виджет идёт по
/// системной локали. Это честнее хардкода и не требует app group ради двух
/// строк на экране блокировки.
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

    /// Локаль для дат, размеров и чисел.
    ///
    /// Подписи берутся отсюда, а даты и байты — из системных форматтеров, и
    /// они смотрят на локаль устройства. На русском интерфейсе это давало
    /// «July 28» под заголовком «Заметка» и «106 KB» вместо «106 КБ». Язык у
    /// приложения один — значит и форматтеры должны спрашивать его же.
    static var locale: Locale { Locale(identifier: lang) }

    static func t(_ ru: String, _ en: String, _ zh: String) -> String {
        switch lang {
        case "ru": return ru
        case "zh": return zh
        default: return en
        }
    }
}
