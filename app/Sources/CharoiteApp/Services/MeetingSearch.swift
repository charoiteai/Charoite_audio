import Foundation

#if os(macOS)

/// Поиск по встречам: найти прошлое решение по слову, не выходя из приложения.
///
/// Окно «Последние встречи» отвечает на «что случилось со вчерашней записью»,
/// но не на «где мы решали про ретеншн». Для второго раньше был только grep
/// или Obsidian. Здесь — лексический поиск по готовым документам встреч:
/// саммари, минуткам, разборам и заметкам графа. Стенограммы не сканируются
/// сознательно: они в десятки раз больше, а решения и поручения к моменту
/// поиска уже вынесены в верхние слои.
enum MeetingSearch {
    enum Kind: Sendable { case meeting, node }

    struct Hit: Identifiable, Equatable, Sendable {
        let file: URL
        /// Человеческое имя места: тема встречи из имени папки или файла.
        let title: String
        /// Строка, где совпало, — обрезанная до читаемой.
        let snippet: String
        /// Дата из имени папки/файла — для сортировки новые первыми.
        /// У узлов графа пустая: они не датированы.
        let day: String
        /// Встреча или узел графа: потребители day и открытие карточки
        /// не должны гадать по пустой строке (ревью 15.08).
        var kind: Kind = .meeting

        var id: String { file.path + snippet }
    }

    /// Какие файлы встречи стоят прочтения при поиске.
    static let layers = ["Саммари.md", "Минутки.md", "Разбор.md"]

    /// Папки узлов графа: русские пишет конвейер, английские — демо-граф.
    static let nodeFolders = ["Люди", "Команды", "Системы", "Модели",
                              "Блокеры", "Ядра", "People", "Teams", "Systems",
                              "Models", "Blockers", "Cores"]

    static func search(_ query: String, graph: URL, limit: Int = 30,
                       includeNodes: Bool = false) -> [Hit] {
        let tokens = tokens(of: query)
        guard !tokens.isEmpty else { return [] }
        var hits: [Hit] = []

        // Узлы графа — канонические точки входа в историю сущности (ревью
        // 15.08): совпадение по ИМЕНИ узла идёт первым, совпадение в теле —
        // после встреч (свежая встреча ценнее строки старой хроники).
        // Имя-ярус не съедает общий limit целиком (ревью ×3).
        var bodyHits: [Hit] = []
        if includeNodes {
            let (byName, byBody) = nodeHits(tokens: tokens, graph: graph)
            hits.append(contentsOf: byName.prefix(min(6, max(1, limit - 1))))
            bodyHits = Array(byBody.prefix(5))
        }

        let archive = graph.appendingPathComponent("Встречи-архив")
        let folders = ((try? FileManager.default.contentsOfDirectory(
            at: archive, includingPropertiesForKeys: nil)) ?? [])
            .filter { !$0.lastPathComponent.hasPrefix("_") }
            .sorted { $0.lastPathComponent > $1.lastPathComponent }   // новые первыми

        for folder in folders {
            guard !Task.isCancelled else { return [] }
            for layer in layers {
                let file = folder.appendingPathComponent(layer)
                guard let hit = match(file: file, tokens: tokens,
                                      title: folder.lastPathComponent) else { continue }
                hits.append(hit)
                break       // одной находки на встречу достаточно: ведём к папке
            }
            if hits.count >= limit { return hits }
        }

        // Заметки графа — на случай встреч, не доехавших до архива.
        let notes = graph.appendingPathComponent("Встречи")
        let noteFiles = ((try? FileManager.default.contentsOfDirectory(
            at: notes, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.pathExtension == "md" }
            .sorted { $0.lastPathComponent > $1.lastPathComponent }
        // дедуп заметок против архива — только по встречам: у узлов day
        // пустой, и общий Set схлопнул бы их в один «день» (ревью 15.08)
        let seenDays = Set(hits.filter { $0.kind == .meeting }.map(\.day))
        for note in noteFiles {
            guard !Task.isCancelled else { return [] }
            guard hits.count < limit else { break }
            let day = dayKey(note.lastPathComponent)
            if seenDays.contains(day) { continue }   // архивная папка уже нашлась
            if let hit = match(file: note, tokens: tokens,
                               title: note.deletingPathExtension().lastPathComponent) {
                hits.append(hit)
            }
        }
        // текстовые совпадения в теле узлов — слабейший ярус, добираются
        // до общего лимита после встреч
        if !bodyHits.isEmpty, hits.count < limit {
            hits.append(contentsOf: bodyHits.prefix(limit - hits.count))
        }
        return hits
    }

    /// Узлы графа по запросу: (совпадения по имени, совпадения в теле).
    ///
    /// Имя сравнивается токенами через стемы ArchiveSearch («платежный» ↔
    /// «Платёжный», «Мироненкой» ↔ «Мироненко»), а не подстроками сырого
    /// filename. Служебные агрегаты (`_ЯДРА.md`, dot-файлы) — не узлы.
    /// Порядок каталога не определён — внутри яруса сортировка по имени.
    static func nodeHits(tokens: [String], graph: URL) -> ([Hit], [Hit]) {
        let want = Set(tokens.map { ArchiveSearch.stem($0) })
        guard !want.isEmpty else { return ([], []) }
        var byName: [Hit] = []
        var byBody: [Hit] = []
        for folder in nodeFolders {
            let dir = graph.appendingPathComponent(folder)
            let files = ((try? FileManager.default.contentsOfDirectory(
                at: dir, includingPropertiesForKeys: nil)) ?? [])
                .filter { $0.pathExtension == "md" }
                .filter { !$0.lastPathComponent.hasPrefix("_")
                    && !$0.lastPathComponent.hasPrefix(".") }
                .sorted { $0.lastPathComponent < $1.lastPathComponent }
            let isPeople = folder == "Люди" || folder == "People"
            for file in files {
                guard !Task.isCancelled else { return (byName, byBody) }
                let name = file.deletingPathExtension().lastPathComponent
                let nameTokens = self.tokens(of: name)
                let nameStems = Set(nameTokens.map { ArchiveSearch.stem($0) })
                // Люди: стеммер режет «Иванов» до «иван», и запрос «Иван»
                // поднимал бы чужой узел верхним ярусом. Токен запроса обязан
                // быть не короче совпавшего токена имени (падежные формы
                // длиннее, чужое короткое имя — нет) — зеркало Python-правила.
                let surfaceOK = !isPeople || tokens.allSatisfy { qt in
                    let qs = ArchiveSearch.stem(qt)
                    let qn = ArchiveSearch.norm(qt)
                    return nameTokens.contains { nt in
                        ArchiveSearch.stem(nt) == qs &&
                        (ArchiveSearch.norm(nt) == qn ||
                         qn.count >= ArchiveSearch.norm(nt).count)
                    }
                }
                if want.isSubset(of: nameStems), surfaceOK {
                    let firstLine = (try? String(contentsOf: file, encoding: .utf8))?
                        .split(separator: "\n")
                        .first { !$0.trimmingCharacters(in: .whitespaces).isEmpty
                            && !$0.hasPrefix("#") && !$0.hasPrefix("---") }
                        .map { snippet(String($0)) } ?? ""
                    byName.append(Hit(file: file, title: "\(name) · \(folder)",
                                      snippet: firstLine, day: "",
                                      kind: .node))
                } else if let hit = match(file: file, tokens: tokens,
                                          title: "\(name) · \(folder)") {
                    byBody.append(Hit(file: hit.file, title: hit.title,
                                      snippet: hit.snippet, day: "",
                                      kind: .node))
                }
            }
        }
        return (byName, byBody)
    }

    /// Чтение архива для SwiftUI: файловый обход выполняется не на главном
    /// акторе, а отмена родительской задачи останавливает и рабочую задачу.
    ///
    /// Сам `search` остаётся синхронным для тестов и служебных вызовов. UI
    /// использует только эту обёртку: на большом графе ввод в поле поиска не
    /// должен ждать чтения десятков файлов.
    static func searchAsync(_ query: String, graph: URL, limit: Int = 30,
                            includeNodes: Bool = false) async -> [Hit] {
        let worker = Task.detached(priority: .userInitiated) {
            search(query, graph: graph, limit: limit, includeNodes: includeNodes)
        }
        return await withTaskCancellationHandler {
            await worker.value
        } onCancel: {
            worker.cancel()
        }
    }

    /// Все ли слова запроса есть в файле; сниппет — первая строка с совпадением.
    static func match(file: URL, tokens: [String], title: String) -> Hit? {
        guard let text = try? String(contentsOf: file, encoding: .utf8) else { return nil }
        let lower = text.lowercased()
        guard tokens.allSatisfy({ lower.contains($0) }) else { return nil }
        let line = text.split(separator: "\n")
            .first { candidate in
                let l = candidate.lowercased()
                return tokens.contains { l.contains($0) }
            }
            .map { snippet(String($0)) } ?? ""
        return Hit(file: file, title: title, snippet: line, day: dayKey(title))
    }

    /// Ключ встречи для дедупа архива и заметок графа.
    ///
    /// Папка называется «2026-08-01 10-00 — Тема», заметка — «2026-08-01_1000»:
    /// сырые префиксы этих имён не совпадают никогда, и дедуп на них молча не
    /// работал — встреча приходила в результатах дважды. Одни цифры совпадают
    /// в обоих форматах: 2026-08-01 10-00 и 2026-08-01_1000 → «202608011000».
    static func dayKey(_ title: String) -> String {
        String(title.filter(\.isNumber).prefix(12))
    }

    static func tokens(of query: String) -> [String] {
        query.lowercased()
            .split { !$0.isLetter && !$0.isNumber }
            .map(String.init)
            .filter { $0.count > 1 }
    }

    /// Строка для списка: без markdown-мусора и не длиннее взгляда.
    static func snippet(_ raw: String) -> String {
        var s = raw.replacingOccurrences(of: "**", with: "")
            .replacingOccurrences(of: "[[", with: "")
            .replacingOccurrences(of: "]]", with: "")
            .trimmingCharacters(in: CharacterSet(charactersIn: " -#>*"))
        if s.count > 140 {
            s = String(s.prefix(140)) + "…"
        }
        return s
    }
}
#endif
