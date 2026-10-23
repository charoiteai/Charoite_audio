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

        /// Имя пути для журнала — только служебная папка корня из `GraphFolders`:
        /// глубже и в имени файла корня — названия встреч, а папку корня называет
        /// пользователь (человек, проект, плоский граф по папкам встреч); журнал
        /// «Забыть встречу» не чистит (финальный Opus M2, круг среза А).
        static func logName(_ rel: String) -> String {
            guard !rel.isEmpty else { return "<корень>" }
            guard rel.contains("/") else { return "<верхний уровень>" }
            let top = String(rel.prefix { $0 != "/" })
            return GraphFolders.all.contains(top) ? top : "<прочее>"
        }

        /// Строка журнала о неполном обходе — одна точка сборки: имена путей
        /// только через `logName`, тест видит ровно то, что уходит в NSLog.
        static func logLine(_ list: [(rel: String, code: String)]) -> String? {
            guard !list.isEmpty else { return nil }
            let shown = list.prefix(3).map { "\(logName($0.rel)) (\($0.code))" }
            return "поиск: обход графа неполный, ошибок \(list.count): " + shown.joined(separator: ", ")
        }

        /// Что живо для prune: увиденное обходом плюс поддеревья, которые не
        /// перечислены, — под ними жив любой ключ, и одна запертая папка не
        /// выключает prune остального графа. Корень не перечислен — nil.
        /// Пустой обход — тоже nil: граф под скрытой папкой, iCloud выгрузил
        /// всё, том не смонтирован — prune стёр бы весь индекс (out2 C1).
        /// Цена: превью последней заметки опустевшего графа живёт до первой
        /// новой заметки.
        func alive(_ keys: Set<String>) -> SemanticIndex.Alive? {
            // путь из графа — аргументом, не в формат: папка «100%s» уронила бы NSLog
            if let line = Self.logLine(list) { NSLog("%@", line) }
            if keys.isEmpty || list.contains(where: { $0.rel.isEmpty }) { return nil }
            return SemanticIndex.Alive(seen: keys, unscanned: list.map(\.rel))
        }
    }
}
