import Foundation

extension ArchiveSearch {
    /// Начало обхода графа для поиска: номер обхода у индекса — до перечисления
    /// каталога (порядок обходов для prune), ошибки перечисления — в `WalkErrors`.
    static func beginGraphWalk(_ graph: URL, keys: [URLResourceKey], index: SemanticIndex) async
        -> (walker: FileManager.DirectoryEnumerator, errors: WalkErrors, seq: UInt64)? {
        let seq = await index.beginWalk()
        let errors = WalkErrors(graph: graph)
        guard let walker = FileManager.default.enumerator(
            at: graph, includingPropertiesForKeys: keys, options: [], errorHandler: errors.record)
        else { return nil }
        return (walker, errors, seq)
    }

    /// Ошибки перечисления обхода графа. Не глушим: подпапка без доступа иначе
    /// молча выпадает из обхода (опыт 23.10: chmod 000 — ни файла, ни сигнала),
    /// а prune индекса принимал её файлы за удалённые (№768). Скрытое по
    /// намерению (`.obsidian`, `.git`) внутри графа — не в счёт. Enumerator
    /// синхронный, обработчик зовётся на том же потоке.
    final class WalkErrors {
        private let graph: URL
        /// rel поддерева, которое не перечислено; "" — сам корень или путь вне графа.
        private(set) var list: [(rel: String, code: String)] = []
        init(graph: URL) { self.graph = graph }

        func record(_ url: URL, _ error: Error) -> Bool {
            // rel — как у обхода: канонический путь от корня графа
            let canon = url.resolvingSymlinksInPath().path
            let rel = canon.hasPrefix(graph.path + "/") ? String(canon.dropFirst(graph.path.count + 1)) : ""
            // скрытое судим по пути внутри графа: граф может лежать под ~/.что-то
            if rel.split(separator: "/").contains(where: { $0.hasPrefix(".") && $0.count > 1 }) { return true }
            let ns = error as NSError
            list.append((rel, "\(ns.domain):\(ns.code)"))
            return true
        }

        /// Что живо для prune: увиденное обходом плюс поддеревья, которые не
        /// перечислены, — под ними жив любой ключ, и одна запертая папка не
        /// выключает prune остального графа. Корень не перечислен — nil.
        /// Пустой обход — тоже nil: граф под скрытой папкой, iCloud выгрузил
        /// всё, том не смонтирован — prune стёр бы весь индекс (out2 C1).
        /// Цена: превью последней заметки опустевшего графа живёт до первой
        /// новой заметки.
        /// Имя пути для журнала — только верхняя папка: глубже и в имени файла
        /// корня — названия встреч, а журнал «Забыть встречу» не чистит
        /// (финальный Opus M2). Путь без «/» — файл или папка корня, не печатаем.
        static func logName(_ rel: String) -> String {
            guard !rel.isEmpty else { return "<корень>" }
            return rel.contains("/") ? String(rel.prefix { $0 != "/" }) : "<верхний уровень>"
        }

        func alive(_ keys: Set<String>) -> SemanticIndex.Alive? {
            if !list.isEmpty {
                let shown = list.prefix(3).map { "\(Self.logName($0.rel)) (\($0.code))" }
                // путь из графа — аргументом, не в формат: папка «100%s» уронила бы NSLog
                NSLog("%@", "поиск: обход графа неполный, ошибок \(list.count): " + shown.joined(separator: ", "))
            }
            if keys.isEmpty || list.contains(where: { $0.rel.isEmpty }) { return nil }
            return SemanticIndex.Alive(seen: keys, unscanned: list.map(\.rel))
        }
    }
}
