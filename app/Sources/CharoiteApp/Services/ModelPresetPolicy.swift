import Foundation

#if os(macOS)

/// Какие модели предложить на ЭТОЙ машине.
///
/// До этого пресеты жили комментарием в config.example.yaml («16GB RAM:
/// model: gemma4:latest») и таблицей в MODELS.md. Человек должен был сам
/// узнать объём своей памяти, найти нужную строчку и перенести значения в
/// конфиг — три шага, на каждом из которых можно ошибиться молча: слишком
/// тяжёлая модель не падает, она просто уходит в своп, и подсказка на
/// встрече приходит через минуту вместо секунды.
///
/// Теперь приложение смотрит на память само и показывает готовые варианты.
struct ModelPreset: Identifiable, Equatable {
    /// Ключ пресета — он же id для SwiftUI.
    let id: String
    /// Название для человека: «Полный», «Сбалансированный», «Лёгкий».
    let title: String
    /// Основная модель — подсказки, саммари, имена.
    let model: String
    /// Лёгкая — тезисы и классификация, работает фоном всю встречу.
    let smallModel: String
    /// Сколько памяти просит связка целиком, ГБ.
    let needsGB: Int
    /// Чем этот вариант отличается — одной фразой.
    let note: String

    /// Модели, которые нужно скачать для этого пресета.
    var models: [String] { [model, smallModel] }
}

enum ModelPresetPolicy {

    /// Все варианты, от тяжёлого к лёгкому.
    ///
    /// Цифры памяти — из docs/MODELS.md и наших замеров, а не на глаз:
    /// qwen3.6:35b-a3b просит ~20 ГБ, gemma4:latest ~9.6, qwen3.5:4b ~3.4,
    /// qwen3.5:2b ~1.8. К связке добавлен запас на систему, STT и браузер:
    /// модель, занявшая всю память, тормозит именно ту встречу, ради которой
    /// её и ставили.
    static let all: [ModelPreset] = [
        ModelPreset(
            id: "full",
            title: L.t("Полный", "Full", "完整"),
            model: "qwen3.6:35b-a3b",
            smallModel: "qwen3.5:4b",
            needsGB: 32,
            note: L.t("Лучшие подсказки и протоколы. Наш рабочий набор.",
                      "Best hints and minutes. Our working set.",
                      "最佳提示与纪要。我们的日常配置。")),
        ModelPreset(
            id: "balanced",
            title: L.t("Сбалансированный", "Balanced", "均衡"),
            model: "gemma4:latest",
            smallModel: "qwen3.5:4b",
            needsGB: 16,
            note: L.t("Заметно легче, качество близкое на коротких встречах.",
                      "Much lighter, close quality on short meetings.",
                      "明显更轻量，短会议质量接近。")),
        ModelPreset(
            id: "light",
            title: L.t("Лёгкий", "Light", "轻量"),
            model: "gemma4:latest",
            smallModel: "qwen3.5:2b",
            needsGB: 8,
            note: L.t("Для 8–16 ГБ: тезисы и протокол, подсказки короче.",
                      "For 8–16 GB: theses and minutes, shorter hints.",
                      "适用于 8–16 GB：要点与纪要，提示更简短。")),
    ]

    /// Память машины в гигабайтах.
    static var machineMemoryGB: Int {
        Int(ProcessInfo.processInfo.physicalMemory / 1_073_741_824)
    }

    /// Что рекомендовать при таком объёме памяти.
    ///
    /// Правило намеренно консервативное: берём пресет, который помещается с
    /// запасом. На 16 ГБ формально влезает и «Полный» — но вместе с системой,
    /// браузером и созвоном он уйдёт в своп, и человек решит, что продукт
    /// медленный, а не что модель не по машине.
    static func recommended(forGB memory: Int) -> ModelPreset {
        if memory >= 32 { return all[0] }
        if memory >= 16 { return all[1] }
        return all[2]
    }

    /// Пресет по ключу — для восстановления выбора из конфига.
    static func preset(id: String) -> ModelPreset? {
        all.first { $0.id == id }
    }

    /// Какой пресет сейчас записан в конфиге.
    ///
    /// Сверяем обе модели: человек мог поправить одну строку руками, и
    /// показывать ему «Полный» с лёгкой моделью было бы неправдой.
    static func current(model: String?, smallModel: String?) -> ModelPreset? {
        guard let model, let smallModel else { return nil }
        return all.first { $0.model == model && $0.smallModel == smallModel }
    }
}

#endif
