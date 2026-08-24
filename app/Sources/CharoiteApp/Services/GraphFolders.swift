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
}
