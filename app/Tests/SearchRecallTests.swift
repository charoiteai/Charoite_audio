import XCTest
@testable import CharoiteApp

/// Полнота поиска: стеммер, который разводит формы одного слова, теряет
/// совпадения молча — файл со словом «meetings» не находится по «meeting».
final class StemConsistencyTests: XCTestCase {
    func testSingularAndPluralStemTheSame() {
        // Однопроходная таблица ["ing","ed","es","s"] режет ровно один
        // суффикс: meetings→meeting, но meeting→meet. Пара «ед/мн число»
        // одного слова получает РАЗНЫЕ стемы, и совпадение между ними
        // не засчитывается никогда.
        XCTAssertEqual(ArchiveSearch.stem("meetings"), ArchiveSearch.stem("meeting"),
                       "meeting и meetings обязаны сходиться к одному стему")
        XCTAssertEqual(ArchiveSearch.stem("settings"), ArchiveSearch.stem("setting"))
        XCTAssertEqual(ArchiveSearch.stem("recordings"), ArchiveSearch.stem("recording"))
        XCTAssertEqual(ArchiveSearch.stem("findings"), ArchiveSearch.stem("finding"))
    }

    func testExistingStemsStayPut() {
        // Якоря: прежние стемы не должны поехать от любого фикса выше.
        XCTAssertEqual(ArchiveSearch.stem("decided"), "decid")
        XCTAssertEqual(ArchiveSearch.stem("blockers"), "blocker")
        XCTAssertEqual(ArchiveSearch.stem("launching"), "launch")
        // русская ветка не задета
        XCTAssertEqual(ArchiveSearch.stem("встречами"), "встреч")
        XCTAssertEqual(ArchiveSearch.stem("мост"), "мост")
    }
}

// MARK: - Общая машинерия семантических тестов

/// Временный граф из (rel, текст)-пар; удалять за собой.
func makeTestGraph(_ files: [(String, String)]) throws -> URL {
    let dir = FileManager.default.temporaryDirectory
        .appendingPathComponent("charoite-graph-\(UUID().uuidString)")
    for (rel, text) in files {
        let url = dir.appendingPathComponent(rel)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try text.write(to: url, atomically: true, encoding: .utf8)
    }
    return dir
}

func tempIndexStore() -> URL {
    FileManager.default.temporaryDirectory
        .appendingPathComponent("charoite-index-\(UUID().uuidString).bin")
}

/// Семантический индекс на тест — свой экземпляр, `shared` тесты не трогают
/// (№534): фоновая доиндексация одного теста больше не прунит индекс
/// следующего. Без `store` — свежий путь в tmp; `loadStore: true` — начать с
/// содержимого файла (перезапуск). В teardown экземпляр закрывается: идущая
/// задача отменена и дождана, после этого файл удаляется — хвост persist
/// не воссоздаёт его. Потолок закрытия 60 с: `close()` отменяет эмбеддер,
/// дольше — повисшая задача, и тест называет себя в отказе.
extension XCTestCase {
    func makeTestIndex(store: URL? = nil, loadStore: Bool = false,
                       file: StaticString = #filePath, line: UInt = #line,
                       embedder: @escaping ([String]) async -> [[Float]]?) -> SemanticIndex {
        let path = store ?? tempIndexStore()
        if loadStore {
            XCTAssertTrue(FileManager.default.fileExists(atPath: path.path),
                          "loadStore без файла — тест проверял бы пустой индекс", file: file, line: line)
        }
        let index = SemanticIndex(store: path, loadStore: loadStore, embedder: embedder)
        let test = name
        addTeardownBlock {
            let closed = await finishesWithin(seconds: 60) { await index.close() }
            if !closed {
                XCTFail("\(test): фоновая доиндексация не закончилась за 60 с", file: file, line: line)
            }
            try? FileManager.default.removeItem(at: path)
        }
        return index
    }
}

/// `true`, если `work` закончилась раньше потолка. Повисшую работу не ждёт.
private func finishesWithin(seconds: UInt64, _ work: @escaping @Sendable () async -> Void) async -> Bool {
    actor Once {
        private var done = false
        func claim() -> Bool { defer { done = true }; return !done }
    }
    let once = Once()
    var timer: Task<Void, Never>?
    let finished: Bool = await withCheckedContinuation { cont in
        Task {
            await work()
            if await once.claim() { cont.resume(returning: true) }
        }
        timer = Task {
            try? await Task.sleep(nanoseconds: seconds * 1_000_000_000)
            if await once.claim() { cont.resume(returning: false) }
        }
    }
    timer?.cancel()
    return finished
}

/// Семантика — не пересортировка лексической выдачи, а второй вход в граф.
///
/// Словарный разрыв — ровно тот случай, ради которого семантический слой
/// существует: вопрос задан другими словами, ни одно слово запроса в файле
/// не встречается. Если кандидаты семантики ограничены файлами, которые уже
/// нашла лексика, такой вопрос не находит ничего никогда — слой оплачен
/// (Ollama, bge-m3, индекс на диске), а его главную работу делать некому.
final class SemanticUnionTests: XCTestCase {
    func testSemanticFindsWhatLexiconMissed() async throws {
        let budgetPath = "Ядра/Бюджет-\(UUID().uuidString).md"
        let graph = try makeTestGraph([
            (budgetPath, "курс валют и деньги на квартал"),
            ("Ядра/Погода.md", "дожди весь месяц"),
        ])
        defer { try? FileManager.default.removeItem(at: graph) }
        // Эмбеддер-подделка: запрос и файл про деньги — один вектор,
        // остальное — ортогональ. Настоящая Ollama не трогается.
        let index = makeTestIndex { texts in
            texts.map { t -> [Float] in
                (t.contains("деньги") || t.contains("финанс")) ? [1, 0] : [0, 1]
            }
        }
        await index.refresh(files: [
            (path: budgetPath, mtime: 1, text: "курс валют и деньги на квартал"),
            (path: "Ядра/Погода.md", mtime: 1, text: "дожди весь месяц"),
        ])

        // Ни «финансовый», ни «прогноз» не встречаются ни в одном файле —
        // лексика пуста, ответ обязан прийти из семантики.
        let out = await ArchiveSearch.localSearch(query: "финансовый прогноз",
                                                  limit: 3, snippet: 200, root: graph, semanticIndex: index)
        XCTAssertTrue(out.contains("Бюджет"),
                      "семантика не смогла добавить файл, которого нет в лексической выдаче; получили: «\(out.prefix(120))»")
        XCTAssertFalse(out.contains("Погода"), "ортогональный файл пролез в выдачу")
    }
}

/// Инвалидация индекса: правка файла обязана делать его эмбеддинг устаревшим.
///
/// В снапшот доиндексации уходит dateTs — а для встреч и daily-заметок это
/// дата из ИМЕНИ файла (fileDate предпочитает её iCloud-mtime). Имя при
/// правке не меняется, значит «mtime» в индексе не меняется никогда:
/// заметка встречи, дополненная после первой индексации, до конца жизни
/// ищется по своему первому черновику. Для свежести ранжирования дата из
/// имени — правильный сигнал; для инвалидации — только настоящий mtime.
final class IndexInvalidationTests: XCTestCase {
    func testEditedMeetingBecomesStaleInIndex() async throws {
        let rel = "Встречи/2026-01-01 планёрка.md"
        let graph = try makeTestGraph([(rel, "обсудили бюджет проекта")])
        defer { try? FileManager.default.removeItem(at: graph) }
        let index = makeTestIndex { texts in
            texts.map { _ -> [Float] in [1, 0] }
        }

        _ = await ArchiveSearch.localSearch(query: "бюджет проекта",
                                            limit: 3, snippet: 200, root: graph, semanticIndex: index)

        // Доиндексация уходит в фон — ждём саму задачу, а не часы. Опрос по
        // часам с потолком 30 с падал на загруженном раннере CI: задача
        // .background стартовала позже, а поздняя задача прошлого теста
        // прунила общий индекс (№412, №534).
        await index.drainForTests()
        let stored = await index.storedMtime(of: rel)
        let got = try XCTUnwrap(stored, "файл не попал в индекс вовсе")

        let attrs = try FileManager.default.attributesOfItem(
            atPath: graph.appendingPathComponent(rel).path)
        let real = try XCTUnwrap(attrs[.modificationDate] as? Date).timeIntervalSince1970

        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd"
        fmt.timeZone = TimeZone(identifier: "UTC")
        let named = try XCTUnwrap(fmt.date(from: "2026-01-01")).timeIntervalSince1970

        XCTAssertGreaterThan(abs(got - named), 86400,
                             "в индекс ушла дата из имени файла — правки не устаревают никогда")
        XCTAssertEqual(got, real, accuracy: 2,
                       "в индексе не настоящий mtime файла")
    }
}

/// Дозировка фона: первый поиск не имеет права зажевать весь граф разом.
///
/// Снапшот доиндексации — весь граф, и это правильно; но refresh без капа
/// ставит в очередь СОТНИ embed-вызовов подряд в ту же Ollama, которая в
/// этот момент обслуживает подсказки живой встречи. Хвост обязан доезжать
/// порциями со следующими поисками, а не одним залпом.
final class RefreshPacingTests: XCTestCase {
    private actor Counter {
        var n = 0
        func bump(_ k: Int) { n += k }
        func value() -> Int { n }
    }

    func testRefreshIsPacedPerCall() async {
        let counter = Counter()
        let index = makeTestIndex { texts in
            await counter.bump(texts.count)
            return texts.map { _ -> [Float] in [1, 0] }
        }
        let files = (0..<100).map { i in
            (path: "Ядра/узел-\(i).md", mtime: 1.0, text: "текст узла \(i)")
        }
        await index.refresh(files: files)
        let first = await counter.value()
        XCTAssertGreaterThan(first, 0, "refresh не сделал ничего")
        XCTAssertLessThanOrEqual(first, 48,
            "один вызов refresh зажевал \(first) файлов из 100 — залп в Ollama посреди встречи")
        await index.refresh(files: files)
        let second = await counter.value()
        XCTAssertGreaterThan(second, first, "второй вызов не продолжил хвост")
    }
}

/// Слот для семантики: находка обязана доехать до выдачи.
///
/// RRF-вес семантики 0.7/(60+rank) НИКОГДА не обгоняет лексический
/// 1/(60+rank) при ranks 0..25 — при ≥limit лексических хитов чисто
/// семантическая находка математически не попадает в выдачу. Объединение
/// без слота работает только на почти пустой лексике; заодно ломается
/// гейт честности: bestSim считается по всему графу, а файл-источник
/// этого bestSim пользователь не видит.
final class SemanticSlotTests: XCTestCase {
    func testSemanticTopSurvivesLexicalFlood() async throws {
        var corpus: [(String, String)] = (1...6).map {
            ("Ядра/Лексика \($0).md", "здесь упоминается контракт номер \($0)")
        }
        corpus.append(("Ядра/Скрытое ядро.md", "суть договорённости другими словами"))
        let graph = try makeTestGraph(corpus)
        defer { try? FileManager.default.removeItem(at: graph) }
        let index = makeTestIndex { texts in
            texts.map { t -> [Float] in
                (t.contains("договорённости") || t == "контракт номер") ? [1, 0] : [0, 1]
            }
        }
        await index.refresh(files: corpus.map {
            (path: $0.0, mtime: 1.0, text: $0.1)
        })

        // 6 лексических попаданий при limit 5 — поток; ответ в скрытом файле
        let out = await ArchiveSearch.localSearch(query: "контракт номер",
                                                  limit: 5, snippet: 200, root: graph, semanticIndex: index)
        XCTAssertTrue(out.contains("Скрытое ядро"),
            "сильная семантическая находка (cos 1.0) не пробилась сквозь лексический поток: \(out.prefix(150))")
    }

    /// Слот семантики стоит в выдаче последним, но держит вердикт: окно
    /// модели не должно выбросить его первым (№743 out1, Sonnet I2).
    func testSemanticSlotIsStrongestPieceForTheWindow() async throws {
        var corpus: [(String, String)] = (1...6).map {
            ("Ядра/Лексика \($0).md", "здесь упоминается контракт номер \($0)")
        }
        corpus.append(("Ядра/Скрытое ядро.md", "суть договорённости другими словами"))
        let graph = try makeTestGraph(corpus)
        defer { try? FileManager.default.removeItem(at: graph) }
        let index = makeTestIndex { texts in
            texts.map { t -> [Float] in
                (t.contains("договорённости") || t == "контракт номер") ? [1, 0] : [0, 1]
            }
        }
        await index.refresh(files: corpus.map {
            (path: $0.0, mtime: 1.0, text: $0.1)
        })
        let out = await ArchiveSearch.localSearchOutcome(query: "контракт номер", limit: 5, snippet: 200,
                                                         budget: 25_000, root: graph, semanticIndex: index)
        let parts = try XCTUnwrap(out.parts)
        let slot = try XCTUnwrap(parts.first { $0.rel == "Ядра/Скрытое ядро.md" })
        // слот гейта — сильнейший кусок: окно выбрасывает по убыванию ранга
        XCTAssertTrue(parts.allSatisfy { $0.rel == slot.rel || $0.rank > slot.rank },
                      "\(parts.map { ($0.rel ?? "", $0.rank) })")
    }

    /// Выдача длиннее бюджета знаков (у чата 25×1000 при 25 000): бюджет упаковщика кончается
    /// раньше выдачи, и слот, стоявший последним, выбрасывался ещё до окна
    /// модели — а вердикт держался на его bestSim (out2, DS C1).
    func testSemanticSlotSurvivesThePackerBudget() async throws {
        var corpus: [(String, String)] = (1...30).map {
            ("Ядра/Лексика \($0).md", "здесь упоминается контракт номер \($0)")
        }
        corpus.append(("Ядра/Скрытое ядро.md", "суть договорённости другими словами"))
        let graph = try makeTestGraph(corpus)
        defer { try? FileManager.default.removeItem(at: graph) }
        let index = makeTestIndex { texts in
            texts.map { t -> [Float] in
                (t.contains("договорённости") || t == "контракт номер") ? [1, 0] : [0, 1]
            }
        }
        await index.refresh(files: corpus.map {
            (path: $0.0, mtime: 1.0, text: $0.1)
        })
        let out = await ArchiveSearch.localSearchOutcome(query: "контракт номер", limit: 25, snippet: 200,
                                                         budget: 1_200, root: graph, semanticIndex: index)
        let parts = try XCTUnwrap(out.parts)
        XCTAssertLessThan(parts.count, 25, "бюджет упаковщика не выбран — тест ничего не проверяет")
        XCTAssertEqual(parts.first { $0.rel == "Ядра/Скрытое ядро.md" }?.rank, -1,
                       "слот гейта выброшен бюджетом выдачи: \(parts.map { $0.rel ?? "" })")
        XCTAssertTrue(out.rels?.contains("Ядра/Скрытое ядро.md") ?? false)
        // срез бюджетом выдачи виден модели, как и срез окном
        XCTAssertTrue(out.note.contains("25"), out.note)
    }

    /// Сильный семантический хит, который и так попал в выдачу, держит вердикт
    /// так же, как добавленный слотом: окно выбрасывает его последним (out3, Sonnet I1).
    func testSemanticTopAlreadyShownIsProtectedToo() async throws {
        var corpus: [(String, String)] = (1...30).map {
            ("Ядра/Лексика \($0).md", "здесь упоминается контракт номер \($0)")
        }
        corpus[6].1 += " и суть договорённости"
        let graph = try makeTestGraph(corpus)
        defer { try? FileManager.default.removeItem(at: graph) }
        let index = makeTestIndex { texts in
            texts.map { t -> [Float] in
                (t.contains("договорённости") || t == "контракт номер") ? [1, 0] : [0, 1]
            }
        }
        await index.refresh(files: corpus.map {
            (path: $0.0, mtime: 1.0, text: $0.1)
        })
        let out = await ArchiveSearch.localSearchOutcome(query: "контракт номер", limit: 25, snippet: 200,
                                                         budget: 25_000, root: graph, semanticIndex: index)
        let parts = try XCTUnwrap(out.parts)
        XCTAssertEqual(parts.first { $0.rel == "Ядра/Лексика 7.md" }?.rank, -1,
                       "\(parts.map { ($0.rel ?? "", $0.rank) })")
    }

    /// Бюджет не взял ни одного куска — это «не проверено» с заметкой, а не
    /// «в памяти нет» (out3, DS M2).
    func testBudgetBelowOnePieceIsNotEmptyMemory() async throws {
        let corpus: [(String, String)] = (1...3).map {
            ("Ядра/Лексика \($0).md", "здесь упоминается контракт номер \($0)")
        }
        let graph = try makeTestGraph(corpus)
        defer { try? FileManager.default.removeItem(at: graph) }
        // семантика сравнила весь граф — без правки это доказанное «пусто»
        let index = makeTestIndex { texts in
            texts.map { $0 == "контракт номер" ? [1, 0] : [0, 1] }
        }
        await index.refresh(files: corpus.map {
            (path: $0.0, mtime: 1.0, text: $0.1)
        })
        let out = await ArchiveSearch.localSearchOutcome(query: "контракт номер", limit: 5, snippet: 200,
                                                         budget: 250, root: graph, semanticIndex: index)
        XCTAssertEqual(out.text, "")
        XCTAssertNotEqual(out.verdict, .empty)
        XCTAssertFalse(out.note.isEmpty)
    }
}

/// Стем не должен зависеть от формы, в которой человек набрал слово.
/// «решений» против «решение», «платежный» против «платёжного» — обе пары
/// раньше расходились, и поиск молча ничего не находил.
final class StemFormTests: XCTestCase {
    func testFormsCollapseToOneStem() {
        // Слова длиной 5-6 букв сюда не годятся: там срабатывает защита
        // «стем не короче 4 символов», и «новый»/«нового» расходятся намеренно —
        // сводить их пришлось бы огрызком «нов», который совпадёт с половиной
        // словаря. Проверяем формы, где стемминг обязан работать.
        let pairs = [("решений", "решения"), ("платежный", "платежного"),
                     ("миграций", "миграции"), ("стратегический", "стратегического")]
        for (a, b) in pairs {
            XCTAssertEqual(ArchiveSearch.stem(ArchiveSearch.norm(a)),
                           ArchiveSearch.stem(ArchiveSearch.norm(b)),
                           "«\(a)» и «\(b)» дают разные стемы — поиск зависит от падежа")
        }
    }
}

/// Конвейер кладёт документы встречи дважды: оригинал в «Документация», копию
/// в «Встречи-архив» для Finder. Поиск обязан видеть их как ОДИН документ.
final class DuplicateContentTests: XCTestCase {
    private func makeGraph() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-dup-\(UUID().uuidString)")
        let orig = root.appendingPathComponent("Документация/Стенограммы встреч")
        let arch = root.appendingPathComponent("Встречи-архив/2026-07-24 09-11 — Постман")
        for d in [orig, arch] {
            try FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        }
        let body = """
        # Встреча про платёжного провайдера
        Решили брать YuPay: комиссия 2.8%, запуск за две недели.
        """
        try body.write(to: orig.appendingPathComponent("2026-07-24_0911_Постман_hints.md"),
                       atomically: true, encoding: .utf8)
        try body.write(to: arch.appendingPathComponent("Подсказки и ответы.md"),
                       atomically: true, encoding: .utf8)
        return root
    }

    func testIdenticalCopyIsNotReturnedTwice() async throws {
        let index = makeTestIndex { _ in nil }
        let root = try makeGraph()
        defer { try? FileManager.default.removeItem(at: root) }

        let out = await ArchiveSearch.localSearch(query: "платёжный провайдер YuPay",
                                                  limit: 5, snippet: 400, root: root, semanticIndex: index)

        let mentions = out.components(separatedBy: "YuPay").count - 1
        XCTAssertEqual(mentions, 1,
                       "один и тот же текст выдан \(mentions) раз — модель получит повтор:\n\(out)")
    }

    func testOriginalWinsOverArchiveCopy() async throws {
        let index = makeTestIndex { _ in nil }
        let root = try makeGraph()
        defer { try? FileManager.default.removeItem(at: root) }

        let out = await ArchiveSearch.localSearch(query: "платёжный провайдер YuPay",
                                                  limit: 5, snippet: 400, root: root, semanticIndex: index)

        XCTAssertTrue(out.contains("Документация/"),
                      "в источниках должен остаться оригинал, а не копия для Finder:\n\(out)")
        XCTAssertFalse(out.contains("Встречи-архив/"),
                       "архивная копия попала в выдачу вместо оригинала")
    }
}

/// Флаг UF_HIDDEN не должен прятать граф от поиска.
///
/// iCloud метит элементы своего контейнера этим флагом (ловили 20.07 —
/// Finder показывал архивную папку пустой). Флаг ложится и на папки графа, а
/// `.skipsHiddenFiles` пропускает их молча. Замер на рабочем графе: обходчик
/// видел 546 файлов из 1172 — целиком пропали «Люди», «Системы», «Встречи»
/// и почти вся «Документация». Поиск отвечал «в памяти этого нет» про людей,
/// с которыми встречи были на этой неделе.
final class HiddenFlagTests: XCTestCase {
    func testHiddenFlaggedFolderIsStillSearched() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-hidden-\(UUID().uuidString)")
        let people = root.appendingPathComponent("Люди")
        try FileManager.default.createDirectory(at: people, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try "# Дмитрий\n\nВедёт витрину сертификации, аналитик."
            .write(to: people.appendingPathComponent("Дмитрий.md"),
                   atomically: true, encoding: .utf8)

        // Ставим флаг ровно так, как это делает iCloud.
        var values = URLResourceValues()
        values.isHidden = true
        var dir = people
        try dir.setResourceValues(values)

        let store = FileManager.default.temporaryDirectory
            .appendingPathComponent("hidden-\(UUID().uuidString).bin")
        let index = makeTestIndex(store: store) { _ in nil }

        let out = await ArchiveSearch.localSearch(query: "кто ведёт витрину сертификации",
                                                  limit: 5, snippet: 400, root: root, semanticIndex: index)
        XCTAssertTrue(out.contains("Дмитрий"),
                      "папка с флагом hidden выпала из поиска — так пропадало полграфа:\n\(out)")
    }

    func testDotFoldersAreStillSkipped() async throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-dot-\(UUID().uuidString)")
        let obsidian = root.appendingPathComponent(".obsidian")
        let notes = root.appendingPathComponent("Ядра")
        for d in [obsidian, notes] {
            try FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
        }
        defer { try? FileManager.default.removeItem(at: root) }
        try "витрина сертификации служебный кэш плагина"
            .write(to: obsidian.appendingPathComponent("cache.md"), atomically: true, encoding: .utf8)
        try "# Витрина\n\nвитрина сертификации — запуск в августе"
            .write(to: notes.appendingPathComponent("Витрина.md"), atomically: true, encoding: .utf8)

        let store = FileManager.default.temporaryDirectory
            .appendingPathComponent("dot-\(UUID().uuidString).bin")
        let index = makeTestIndex(store: store) { _ in nil }

        let out = await ArchiveSearch.localSearch(query: "витрина сертификации",
                                                  limit: 5, snippet: 400, root: root, semanticIndex: index)
        XCTAssertTrue(out.contains("Ядра/"), "нужный узел не найден")
        XCTAssertFalse(out.contains(".obsidian"),
                       "служебная папка плагина попала в выдачу")
    }
}

/// Вердикт локального поиска (№634): «пусто» — только когда семантика
/// проверила граф. Индекс пуст или эмбеддер молчит — «не проверено», и чат
/// не скажет «в памяти этого нет» про то, чего не искал.
final class LocalVerdictTests: XCTestCase {
    func testVerdictSeparatesEmptyFromUnverified() async throws {
        let graph = try makeTestGraph([("Ядра/Погода.md", "дожди весь месяц")])
        defer { try? FileManager.default.removeItem(at: graph) }

        // Эмбеддер молчит: семантика не отработала.
        var index = makeTestIndex { _ in nil }
        let silent = await ArchiveSearch.localSearchOutcome(query: "квартальный бюджет", limit: 3,
                                                            snippet: 200, root: graph, semanticIndex: index)
        XCTAssertEqual(silent.text, "")
        XCTAssertEqual(silent.verdict, .unverified)
        // сильная лексика без семантики — `confident`, как в базе (долг №637)
        let wordsOnly = await ArchiveSearch.localSearchOutcome(query: "дожди месяц", limit: 3,
                                                               snippet: 200, root: graph, semanticIndex: index)
        XCTAssertFalse(wordsOnly.text.isEmpty)
        XCTAssertFalse(wordsOnly.text.hasPrefix(ArchiveSearch.lowConfidenceMarker))
        XCTAssertEqual(wordsOnly.verdict, .confident)

        // Эмбеддер отвечает, похожего нет: пусто проверено.
        index = makeTestIndex(embedder: { texts in
            texts.map { $0.contains("бюджет") ? [1, 0] : [0, 1] }
        })
        await index.refresh(files: [(path: "Ядра/Погода.md", mtime: 1, text: "дожди весь месяц")])
        let checked = await ArchiveSearch.localSearchOutcome(query: "квартальный бюджет", limit: 3,
                                                             snippet: 200, root: graph, semanticIndex: index)
        XCTAssertEqual(checked.text, "")
        XCTAssertEqual(checked.verdict, .empty)

        let missing = await ArchiveSearch.localSearchOutcome(
            query: "бюджет", root: graph.appendingPathComponent("нет-такой-папки"), semanticIndex: index)
        XCTAssertEqual(missing.verdict, .unavailable)

        let found = await ArchiveSearch.localSearchOutcome(query: "дожди месяц", limit: 3,
                                                           snippet: 200, root: graph, semanticIndex: index)
        XCTAssertFalse(found.text.isEmpty)
        XCTAssertEqual(found.verdict, .confident)
    }

    /// Индекс полон, а эмбеддер лёг к запросу — тоже «не проверено»: прежний
    /// тест ловил nil пустого индекса и до молчания эмбеддера не доходил
    /// (out1, Haiku M1).
    func testSilentEmbedderOverFilledIndexIsUnverified() async throws {
        let graph = try makeTestGraph([("Ядра/Погода.md", "дожди весь месяц")])
        defer { try? FileManager.default.removeItem(at: graph) }
        let store = tempIndexStore()
        let filled = makeTestIndex(store: store, embedder: { $0.map { $0.contains("бюджет") ? [1, 0] : [0, 1] } })
        await filled.refresh(files: [(path: "Ядра/Погода.md", mtime: 1, text: "дожди весь месяц")])
        await filled.close()
        let index = makeTestIndex(store: store, loadStore: true) { _ in nil }
        let kept = await index.count()
        XCTAssertEqual(kept, 1, "индекс должен остаться полным")
        let out = await ArchiveSearch.localSearchOutcome(query: "квартальный бюджет", limit: 3,
                                                         snippet: 200, root: graph, semanticIndex: index)
        XCTAssertEqual(out.text, "")
        XCTAssertEqual(out.verdict, .unverified)
    }

    /// Поиск, который не прошёл граф, «пусто» не доказывает (out1, Sonnet C1,
    /// DS I1): запрос без игл, непрочитанные файлы, векторы другой модели,
    /// индекс, собранный не весь.
    func testSearchThatDidNotCoverTheGraphIsUnverified() async throws {
        // свой индекс на каждый случай: поиск ставит фоновый refresh, и
        // поздний refresh прошлого случая дособрал бы индекс следующего (№534)
        let graph = try makeTestGraph([("Ядра/Погода.md", "дожди весь месяц"),
                                       ("Встречи/2026-01-01.md", "обсудили отпуск")])
        defer { try? FileManager.default.removeItem(at: graph) }
        let files: [(path: String, mtime: Double, text: String)] = [
            ("Ядра/Погода.md", 1, "дожди весь месяц"), ("Встречи/2026-01-01.md", 1, "обсудили отпуск")]

        var index = makeTestIndex(embedder: { $0.map { $0.contains("бюджет") ? [1, 0] : [0, 1] } })
        await index.refresh(files: files)
        let noNeedles = await ArchiveSearch.localSearchOutcome(query: "кто я", limit: 3,
                                                               snippet: 200, root: graph, semanticIndex: index)
        XCTAssertEqual(noNeedles.verdict, .unverified, "запрос без игл — поиска не было")

        // векторы индекса другой размерности: ни один блок не сравнился
        let store = tempIndexStore()
        index = makeTestIndex(store: store, embedder: { $0.map { $0.contains("бюджет") ? [1, 0] : [0, 1] } })
        await index.refresh(files: files)
        await index.close()
        index = makeTestIndex(store: store, loadStore: true, embedder: { $0.map { $0.contains("бюджет") ? [1, 0, 0] : [0, 1, 0] } })
        let otherModel = await ArchiveSearch.localSearchOutcome(query: "квартальный бюджет", limit: 3,
                                                                snippet: 200, root: graph, semanticIndex: index)
        XCTAssertEqual(otherModel.verdict, .unverified, "векторы другой модели — семантика не проверила")

        // в индексе один файл из двух: проверена половина графа
        index = makeTestIndex(embedder: { $0.map { $0.contains("бюджет") ? [1, 0] : [0, 1] } })
        await index.refresh(files: [files[0]])
        let partial = await ArchiveSearch.localSearchOutcome(query: "квартальный бюджет", limit: 3,
                                                             snippet: 200, root: graph, semanticIndex: index)
        XCTAssertEqual(partial.verdict, .unverified, "индекс собран не весь")

        // файлы есть, но ни один не прочитался; свой граф — кэш текстов
        // помнит прочитанное выше
        let shut = try makeTestGraph([("Ядра/Закрыто.md", "дожди весь месяц"),
                                      ("Встречи/2026-01-02.md", "обсудили отпуск")])
        defer { try? FileManager.default.removeItem(at: shut) }
        let locked = shut.appendingPathComponent("Ядра/Закрыто.md")
        let other = shut.appendingPathComponent("Встречи/2026-01-02.md")
        try FileManager.default.setAttributes([.posixPermissions: 0], ofItemAtPath: locked.path)
        try FileManager.default.setAttributes([.posixPermissions: 0], ofItemAtPath: other.path)
        defer {
            try? FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: locked.path)
            try? FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: other.path)
        }
        index = makeTestIndex(embedder: { $0.map { $0.contains("бюджет") ? [1, 0] : [0, 1] } })
        await index.refresh(files: [("Ядра/Закрыто.md", 1, "дожди весь месяц"),
                                    ("Встречи/2026-01-02.md", 1, "обсудили отпуск")])
        let unread = await ArchiveSearch.localSearchOutcome(query: "квартальный бюджет", limit: 3,
                                                            snippet: 200, root: shut, semanticIndex: index)
        XCTAssertEqual(unread.verdict, .unverified, "ни один файл не прочитан: \(unread.text)")

        // прочитана половина графа: неоткрывшаяся заметка в знаменателе охвата
        // (out2, C1 трёх голов: 1 из 1 прочитанного «доказывал» пусто)
        let half = try makeTestGraph([("Ядра/Половина.md", "дожди весь месяц"),
                                      ("Встречи/2026-01-03.md", "обсудили отпуск")])
        defer { try? FileManager.default.removeItem(at: half) }
        let shutHalf = half.appendingPathComponent("Встречи/2026-01-03.md")
        try FileManager.default.setAttributes([.posixPermissions: 0], ofItemAtPath: shutHalf.path)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: shutHalf.path) }
        index = makeTestIndex(embedder: { $0.map { $0.contains("бюджет") ? [1, 0] : [0, 1] } })
        await index.refresh(files: [("Ядра/Половина.md", 1, "дожди весь месяц"),
                                    ("Встречи/2026-01-03.md", 1, "обсудили отпуск")])
        let halfRead = await ArchiveSearch.localSearchOutcome(query: "квартальный бюджет", limit: 3,
                                                              snippet: 200, root: half, semanticIndex: index)
        XCTAssertEqual(halfRead.verdict, .unverified, "половина графа не прочитана: \(halfRead.text)")
    }

    /// Две ссылки наружу графа с одним именем схлопываются в одну rel, а
    /// индекс под этим ключом держит текст одной заметки: охват считается по
    /// документам, и непроверенная вторая не даёт «пусто» (out4, DS C1 —
    /// откат out3 M7).
    func testCollapsedRelDoesNotProveCoverage() async throws {
        let outside = try makeTestGraph([("a/README.md", "дожди весь месяц"), ("b/README.md", "отпуск перенесли на август")])
        let graph = try makeTestGraph([])
        defer { try? FileManager.default.removeItem(at: outside); try? FileManager.default.removeItem(at: graph) }
        for sub in ["a", "b"] {
            let link = graph.appendingPathComponent("\(sub)/README.md")
            try FileManager.default.createDirectory(at: link.deletingLastPathComponent(), withIntermediateDirectories: true)
            try FileManager.default.createSymbolicLink(at: link, withDestinationURL: outside.appendingPathComponent("\(sub)/README.md"))
        }
        let embed: ([String]) -> [[Float]] = { $0.map { $0.contains("каникулы") || $0.contains("отпуск") ? [1, 0] : [0, 1] } }
        let index = makeTestIndex(embedder: { embed($0) })
        await index.refresh(files: [("README.md", 1, "дожди весь месяц")])
        let out = await ArchiveSearch.localSearchOutcome(query: "каникулы", limit: 3,
                                                         snippet: 200, root: graph, semanticIndex: index)
        XCTAssertTrue(out.text.isEmpty, out.text)
        XCTAssertEqual(out.verdict, .unverified, out.text)

        // пара: те же два текста под разными именами, оба в индексе, семантика
        // сравнила оба — «пусто» доказано; разница с первым случаем — знаменатель (out5, DS M2)
        let named = try makeTestGraph([("a/Дожди.md", "дожди весь месяц"), ("b/Отпуск.md", "отпуск перенесли на август")])
        defer { try? FileManager.default.removeItem(at: named) }
        let both = makeTestIndex(
                               embedder: { $0.map { $0.contains("снег") ? [1, 0] : [0, 1] } })
        await both.refresh(files: [("a/Дожди.md", 1, "дожди весь месяц"), ("b/Отпуск.md", 1, "отпуск перенесли на август")])
        let proven = await ArchiveSearch.localSearchOutcome(query: "снег", limit: 3,
                                                            snippet: 200, root: named, semanticIndex: both)
        XCTAssertTrue(proven.text.isEmpty, proven.text)
        XCTAssertEqual(proven.verdict, .empty)
    }
}
