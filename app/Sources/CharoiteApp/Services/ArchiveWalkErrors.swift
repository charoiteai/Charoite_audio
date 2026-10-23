import Foundation

extension ArchiveSearch {
    /// Ошибки перечисления обхода графа. Не глушим: подпапка без доступа иначе
    /// молча выпадает из обхода (опыт 23.10: chmod 000 — ни файла, ни сигнала),
    /// а prune индекса принимал её файлы за удалённые (№768). Скрытое по
    /// намерению (`.obsidian`, `.git`) — не в счёт. Enumerator синхронный,
    /// обработчик зовётся на том же потоке.
    final class WalkErrors {
        private let graph: URL
        private(set) var list: [(rel: String, code: Int)] = []
        init(graph: URL) { self.graph = graph }

        func record(_ url: URL, _ error: Error) -> Bool {
            if !url.pathComponents.contains(where: { $0.hasPrefix(".") && $0.count > 1 }) {
                // в журнал — путь от корня графа, как rel обхода, не путь машины
                let canon = url.resolvingSymlinksInPath().path
                let rel = canon.hasPrefix(graph.path + "/")
                    ? String(canon.dropFirst(graph.path.count + 1)) : url.lastPathComponent
                list.append((rel, (error as NSError).code))
            }
            return true
        }

        /// Ключи для prune, если обход полный; иначе nil и строка журнала.
        func alive(_ keys: Set<String>) -> Set<String>? {
            guard !list.isEmpty else { return keys }
            NSLog("поиск: обход графа неполный, ошибок \(list.count): "
                  + list.prefix(3).map { "\($0.rel) (\($0.code))" }.joined(separator: ", "))
            return nil
        }
    }
}
