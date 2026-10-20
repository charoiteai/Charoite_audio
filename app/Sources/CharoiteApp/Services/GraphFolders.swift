import Foundation

/// Контракт верхних папок графа — зеркало Python-конвейера (graph_updater
/// трёх языков: demo/graph, graph_en, graph_zh) плюс архив встреч. ЕДИНАЯ
/// таблица для чипов источников, инвентаря колонки и любого будущего
/// потребителя. Живёт в слое данных: круг-2 по PR #396 (GLM) — контракт в
/// вью-политике заставлял сервисы тянуть знание из UI, и это была уже
/// третья таблица папок в кодовой базе.
enum GraphFolders {
    static let meetings = ["Встречи", "Встречи-архив", "Meetings", "Meetings-archive", "会议", "会议归档"]
    static let nodes = ["Люди", "Системы", "Команды", "Блокеры", "Модели", "Ядра",
                        "People", "Systems", "Teams", "Blockers", "Models", "Cores",
                        "人物", "系统", "团队", "阻碍", "模型", "核心"]
    static let dossiers = ["Досье", "Dossiers", "档案"]
    static let docs = ["Документация", "Docs", "文档"]
    static let cores = ["Ядра", "Cores", "核心"]
    static var all: [String] { meetings + nodes + dossiers + docs }

    /// Префиксы служебного файла — зеркало `service_prefixes` схемы Чароита
    /// (src/charoite_schema.py; тест сверяет литерал). Указатели папок, карта
    /// содержания, кандидаты, индекс досье пишет конвейер: это навигация, не
    /// документ, и Python-поиск держит их вне индекса (`doc_role` → SERVICE).
    /// Правило ИМЕНИ ФАЙЛА: папки с «_» оно не судит.
    static let servicePrefixes = ["_", "Служебное_"]

    /// Служебный ли файл — по имени (последний компонент пути), в форме
    /// сравнения Python-схемы: единая форма Unicode, регистр, ё→е.
    static func isServiceFile(_ rel: String) -> Bool {
        let name = (rel as NSString).lastPathComponent
        let f = fold(name)
        return servicePrefixes.contains { f.hasPrefix(fold($0)) }
    }

    /// Карта содержания в корне графа. Служебная по имени, но источником чата
    /// остаётся (№750, бенч навигации: без неё 7/13 фактов против 10/13) —
    /// обзорные факты лежат в ней одной; чип подписан «Карта графа».
    static let mapFile = "_MOC.md"

    /// Карта ли это — путь ОТ КОРНЯ графа, а не имя: «Встречи/_MOC.md» не карта.
    static func isMapFile(_ rel: String) -> Bool { fold(rel) == fold(mapFile) }

    /// Не источник чата: служебный файл, кроме карты содержания — указатели
    /// папок («_ЯДРА», «_ИНДЕКС»…) и прочая навигация конвейера.
    static func isChatExcluded(_ rel: String) -> Bool { isServiceFile(rel) && !isMapFile(rel) }

    private static func fold(_ s: String) -> String {
        s.precomposedStringWithCanonicalMapping.lowercased()
            .replacingOccurrences(of: "ё", with: "е")
    }
}
