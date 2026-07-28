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
        .appendingPathComponent("charoite-index-\(UUID().uuidString).json")
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
        let graph = try makeTestGraph([
            ("Ядра/Бюджет.md", "курс валют и деньги на квартал"),
            ("Ядра/Погода.md", "дожди весь месяц"),
        ])
        defer { try? FileManager.default.removeItem(at: graph) }
        // Эмбеддер-подделка: запрос и файл про деньги — один вектор,
        // остальное — ортогональ. Настоящая Ollama не трогается.
        await SemanticIndex.shared.useForTests(store: tempIndexStore()) { texts in
            texts.map { t -> [Float] in
                (t.contains("деньги") || t.contains("финанс")) ? [1, 0] : [0, 1]
            }
        }
        await SemanticIndex.shared.refresh(files: [
            (path: "Ядра/Бюджет.md", mtime: 1, text: "курс валют и деньги на квартал"),
            (path: "Ядра/Погода.md", mtime: 1, text: "дожди весь месяц"),
        ])

        // Ни «финансовый», ни «прогноз» не встречаются ни в одном файле —
        // лексика пуста, ответ обязан прийти из семантики.
        let out = await ArchiveSearch.localSearch(query: "финансовый прогноз",
                                                  limit: 3, snippet: 200, root: graph)
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
        await SemanticIndex.shared.useForTests(store: tempIndexStore()) { texts in
            texts.map { _ -> [Float] in [1, 0] }
        }

        _ = await ArchiveSearch.localSearch(query: "бюджет проекта",
                                            limit: 3, snippet: 200, root: graph)

        // доиндексация уходит в фоновую Task — дожидаемся записи в индексе
        // (запас до 10 секунд: раннер CI бывает занят соседями)
        var stored: Double?
        for _ in 0..<500 {
            stored = await SemanticIndex.shared.storedMtime(of: rel)
            if stored != nil { break }
            try await Task.sleep(nanoseconds: 20_000_000)
        }
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
        await SemanticIndex.shared.useForTests(store: tempIndexStore()) { texts in
            await counter.bump(texts.count)
            return texts.map { _ -> [Float] in [1, 0] }
        }
        let files = (0..<100).map { i in
            (path: "Ядра/узел-\(i).md", mtime: 1.0, text: "текст узла \(i)")
        }
        await SemanticIndex.shared.refresh(files: files)
        let first = await counter.value()
        XCTAssertGreaterThan(first, 0, "refresh не сделал ничего")
        XCTAssertLessThanOrEqual(first, 48,
            "один вызов refresh зажевал \(first) файлов из 100 — залп в Ollama посреди встречи")
        await SemanticIndex.shared.refresh(files: files)
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
        await SemanticIndex.shared.useForTests(store: tempIndexStore()) { texts in
            texts.map { t -> [Float] in
                (t.contains("договорённости") || t == "контракт номер") ? [1, 0] : [0, 1]
            }
        }
        await SemanticIndex.shared.refresh(files: corpus.map {
            (path: $0.0, mtime: 1.0, text: $0.1)
        })

        // 6 лексических попаданий при limit 5 — поток; ответ в скрытом файле
        let out = await ArchiveSearch.localSearch(query: "контракт номер",
                                                  limit: 5, snippet: 200, root: graph)
        XCTAssertTrue(out.contains("Скрытое ядро"),
            "сильная семантическая находка (cos 1.0) не пробилась сквозь лексический поток: \(out.prefix(150))")
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
