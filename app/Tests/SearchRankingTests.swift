import XCTest
@testable import CharoiteApp

/// Первые тесты пакета: ранжирование поиска — чистая логика без UI.
final class SearchRankingTests: XCTestCase {
    func testStemUnifiesWordForms() {
        XCTAssertEqual(ArchiveSearch.stem("встреча"), "встреч")
        XCTAssertEqual(ArchiveSearch.stem("встречах"), "встреч")
        XCTAssertEqual(ArchiveSearch.stem("встречами"), "встреч")
    }

    func testStemKeepsShortWords() {
        XCTAssertEqual(ArchiveSearch.stem("мост"), "мост")
        XCTAssertEqual(ArchiveSearch.stem("тема"), "тема")
    }

    func testNormFoldsYo() {
        XCTAssertEqual(ArchiveSearch.norm("Тёплый"), ArchiveSearch.norm("теплый"))
    }

    func testMarkdownRendersBoldWithoutAsterisks() {
        let out = MarkdownLine.render("это **важно** знать")
        XCTAssertFalse(String(out.characters).contains("**"))
        XCTAssertTrue(String(out.characters).contains("важно"))
    }

    func testMarkdownHeadingStripsHashes() {
        let out = MarkdownLine.render("## Решения")
        XCTAssertEqual(String(out.characters), "Решения")
    }
}

/// Задачи: парсинг чекбоксов и отметка пишутся в файл корректно.
@MainActor
final class TasksServiceTests: XCTestCase {
    func testScanAndToggleRoundtrip() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("Минутки.md")
        try """
        # Минутки
        ## Поручения
        - [ ] **Мария** — подписать договор — до 31.07
        - [x] **Игорь** — прислать сравнение — сделано
        обычная строка без чекбокса
        """.write(to: file, atomically: true, encoding: .utf8)

        // scanSync, а не rescan: rescan уводит работу в фон (полный обход
        // графа морозил интерфейс), и тест не должен зависеть от планировщика.
        var found = TasksService.scanSync(graph: dir)
        XCTAssertEqual(found.count, 2)
        XCTAssertEqual(found.filter { !$0.done }.count, 1)

        let open = try XCTUnwrap(found.first { !$0.done })
        XCTAssertEqual(TasksService.toggleSync(open), .changed)
        let text = try String(contentsOf: file, encoding: .utf8)
        XCTAssertTrue(text.contains("- [x] **Мария**"))

        // обратно в открытую
        found = TasksService.scanSync(graph: dir)
        let done = try XCTUnwrap(found.first { $0.text.contains("Мария") })
        XCTAssertEqual(TasksService.toggleSync(done), .changed)
        found = TasksService.scanSync(graph: dir)
        XCTAssertEqual(found.filter { !$0.done }.count, 1)
    }

    func testToggleFindsTheSameTaskAfterExternalLinesWereInserted() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-tasks-move-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("Минутки.md")
        try "# Минутки\n- [ ] Мария — подписать договор\n".write(
            to: file, atomically: true, encoding: .utf8)
        let item = try XCTUnwrap(TasksService.scanSync(graph: dir).first)

        // Obsidian вставил строку после скана: прежний lineIndex теперь
        // указывает на заголовок, но текст поручения всё ещё однозначен.
        try "новая строка\n# Минутки\n- [ ] Мария — подписать договор\n".write(
            to: file, atomically: true, encoding: .utf8)

        XCTAssertEqual(TasksService.toggleSync(item), .changed)
        let saved = try String(contentsOf: file, encoding: .utf8)
        XCTAssertTrue(saved.contains("- [x] Мария — подписать договор"))
        XCTAssertTrue(saved.hasPrefix("новая строка"))
    }

    func testToggleRefusesAmbiguousDuplicateAfterExternalEdit() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-tasks-conflict-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = dir.appendingPathComponent("Минутки.md")
        let line = "- [ ] Мария — подписать договор"
        try "# Минутки\n\(line)\n".write(to: file, atomically: true, encoding: .utf8)
        let item = try XCTUnwrap(TasksService.scanSync(graph: dir).first)

        // Строка сдвинулась и теперь встречается дважды: выбирать одну наугад
        // нельзя, обе остаются открытыми.
        try "вставка\n# Минутки\n\(line)\n\(line)\n".write(
            to: file, atomically: true, encoding: .utf8)

        XCTAssertEqual(TasksService.toggleSync(item), .conflict)
        let saved = try String(contentsOf: file, encoding: .utf8)
        XCTAssertEqual(saved.components(separatedBy: "- [ ]").count - 1, 2)
    }

    func testMeetingLinkMatchesArchiveAndStatusFormats() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-task-link-\(UUID().uuidString)")
        let folder = dir.appendingPathComponent(
            "Встречи-архив/2026-08-04 11-31 — Планирование")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        try "- [ ] Мария — проверить сборку\n".write(
            to: folder.appendingPathComponent("Минутки.md"),
            atomically: true, encoding: .utf8)
        let item = try XCTUnwrap(TasksService.scanSync(graph: dir).first)

        XCTAssertTrue(TasksService.belongs(item, to: "2026-08-04_113145"))
        XCTAssertFalse(TasksService.belongs(item, to: "2026-08-04_1200"))
        XCTAssertEqual(TasksService.sourceTitle(item.rel), "Планирование")
    }

    func testMeetingCardPrefersMinutesOverDuplicateGraphTask() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-task-source-\(UUID().uuidString)")
        let archive = dir.appendingPathComponent(
            "Встречи-архив/2026-08-04 11-31 — Планирование")
        let notes = dir.appendingPathComponent("Встречи")
        try FileManager.default.createDirectory(at: archive, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: notes, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        try "- [x] Мария — проверить сборку\n".write(
            to: archive.appendingPathComponent("Минутки.md"),
            atomically: true, encoding: .utf8)
        try "- [ ] Мария — проверить сборку\n".write(
            to: notes.appendingPathComponent("2026-08-04_1131.md"),
            atomically: true, encoding: .utf8)

        let all = TasksService.scanSync(graph: dir)
        XCTAssertEqual(all.count, 1, "дубль заметки не должен раздувать общий счётчик")
        let card = TasksService.meetingItems(all, for: "2026-08-04_113145")
        XCTAssertEqual(card.count, 1)
        XCTAssertTrue(card[0].done)
        XCTAssertTrue(TasksService.meetingItems(
            all, for: "2026-08-04_113145", includeDone: false).isEmpty)
    }
}

/// Интеграция: гибридный поиск на демо-графе из репозитория.
final class DemoGraphSearchTests: XCTestCase {
    override func setUp() async throws {
        // Пустой индекс во временном файле и эмбеддер «Ollama лежит»: тест
        // детерминирован (чистая лексика, как в CI) и не пишет эмбеддинги
        // демо-графа в настоящий индекс на машине с поднятой Ollama.
        let store = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-demo-index-\(UUID().uuidString).json")
        await SemanticIndex.shared.useForTests(store: store) { _ in nil }
    }

    private var demoGraph: URL {
        // app/Tests/… → корень репо → demo/graph
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // Tests
            .deletingLastPathComponent()   // app
            .deletingLastPathComponent()   // repo root
            .appendingPathComponent("demo/graph")
    }

    func testDecisionQuestionFindsPaymentNodes() async {
        let out = await ArchiveSearch.localSearch(
            query: "что решили по платёжному провайдеру?", limit: 5, snippet: 600,
            root: demoGraph)
        XCTAssertTrue(out.contains("платёжного провайдера") || out.contains("Платёжный шлюз"),
                      "ожидали узлы про платёжного провайдера, получили: \(out.prefix(200))")
        XCTAssertTrue(out.lowercased().contains("юpay".lowercased()),
                      "факт-ответ (ЮPay) должен попасть в сниппеты")
    }

    func testBlockerQuestionFindsBlockerNode() async {
        let out = await ArchiveSearch.localSearch(
            query: "какие блокеры сейчас?", limit: 3, snippet: 400, root: demoGraph)
        XCTAssertTrue(out.contains("Блокеры/"), "узел блокера должен быть в выдаче")
    }

    func testOffTopicGetsLowConfidenceOrEmpty() async {
        let out = await ArchiveSearch.localSearch(
            query: "рецепт борща с пампушками", limit: 3, snippet: 300, root: demoGraph)
        XCTAssertTrue(out.isEmpty || out.hasPrefix(ArchiveSearch.lowConfidenceMarker),
                      "офтопик обязан быть пустым или с «⚠», получили: \(out.prefix(120))")
    }
}

/// Персист истории ответов: лимит и roundtrip на диске.
@MainActor
final class ArchiveHistoryStoreTests: XCTestCase {
    func testAppendPersistsAndLimits() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-history-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }

        let store = ArchiveHistoryStore.shared
        store.clear(root: dir)
        for i in 1...55 {
            store.append(q: "вопрос \(i)", a: "ответ \(i)", root: dir)
        }
        XCTAssertEqual(store.entries.count, 50, "лимит 50 записей")
        XCTAssertEqual(store.entries.last?.q, "вопрос 55")
        XCTAssertEqual(store.entries.first?.q, "вопрос 6", "старые срезаны")

        // roundtrip: файл читается заново
        let data = try Data(contentsOf: dir.appendingPathComponent("archive_history.json"))
        let decoded = try JSONDecoder().decode([ArchiveHistoryStore.Entry].self, from: data)
        XCTAssertEqual(decoded.count, 50)
        store.clear(root: dir)
    }
}

/// Английский поиск: en-стемминг и e2e по английскому демо-графу.
final class EnglishSearchTests: XCTestCase {
    override func setUp() async throws {
        // см. DemoGraphSearchTests.setUp — те же причины
        let store = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-demo-index-\(UUID().uuidString).json")
        await SemanticIndex.shared.useForTests(store: store) { _ in nil }
    }

    func testEnglishStemming() {
        XCTAssertEqual(ArchiveSearch.stem("decided"), "decid")
        XCTAssertEqual(ArchiveSearch.stem("blockers"), "blocker")
        XCTAssertEqual(ArchiveSearch.stem("launching"), "launch")
        // русская ветка не задета
        XCTAssertEqual(ArchiveSearch.stem("встречах"), "встреч")
    }

    private var demoGraphEn: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent("demo/graph_en")
    }

    func testEnglishDecisionQuestion() async {
        let out = await ArchiveSearch.localSearch(
            query: "what did we decide about the payment provider?",
            limit: 5, snippet: 600, root: demoGraphEn)
        XCTAssertTrue(out.contains("Payment"), "payment nodes expected: \(out.prefix(200))")
        XCTAssertTrue(out.contains("YuPay"), "the factual answer must reach snippets")
    }

    func testEnglishBlockerQuestion() async {
        let out = await ArchiveSearch.localSearch(
            query: "what are the current blockers?", limit: 3, snippet: 400,
            root: demoGraphEn)
        XCTAssertTrue(out.contains("Blockers/"), "blocker node expected")
    }

    /// Гейт честности обязан молчать, когда ответ в архиве ЕСТЬ.
    ///
    /// Проверка была только обратная — «офтопик получает пометку», и она
    /// проходила тривиально. Тем временем служебные слова (what/did/the)
    /// считались в покрытие запроса, и оба канонических вопроса из README
    /// возвращались с «⚠ возможно, в архиве этого нет» плюс инструкцией
    /// синтезу не доверять найденному. Продукт объявлял безответным вопрос,
    /// ответ на который лежал в первом же сниппете.
    func testOnTopicQuestionIsNotFlaggedLowConfidence() async {
        for query in ["what did we decide about the payment provider?",
                      "what are the current blockers?"] {
            let out = await ArchiveSearch.localSearch(
                query: query, limit: 5, snippet: 600, root: demoGraphEn)
            XCTAssertFalse(out.hasPrefix(ArchiveSearch.lowConfidenceMarker),
                           "вопрос по существу помечен как безответный: \(query)")
        }
    }

    /// Служебные слова не должны попадать в иглы: они есть почти в каждом
    /// файле и занижают покрытие, из-за чего срабатывает гейт честности.
    func testStopWordsAreNotNeedles() {
        for word in ["what", "the", "did", "current", "какие", "статус"] {
            XCTAssertTrue(ArchiveSearch.isStopWord(word),
                          "«\(word)» считается значимым словом запроса")
        }
        XCTAssertFalse(ArchiveSearch.isStopWord("payment"))
        XCTAssertFalse(ArchiveSearch.isStopWord("блокер"))
    }
}

/// Вес документа по роли в конвейере.
///
/// Замер по рабочему графу: демпфер видел только треть сырья. Стенограммы
/// (290 файлов, 8.2 МБ) он ловил, а «Подсказки и ответы» (2.4 МБ), «Вопросы и
/// ответы» (1.7 МБ) и черновики (1.1 МБ) конкурировали с узлами графа на
/// равных. Минутки — готовая выжимка решений, ровно то, за чем приходят с
/// вопросом «что решили», — не имели никакого преимущества.
final class DocumentRoleWeightTests: XCTestCase {
    func testRawMaterialIsDamped() {
        for path in ["Документация/Стенограммы встреч/2026-07-20_1000.md",
                     "Встречи-архив/2026-07-20 Витрина/Подсказки и ответы.md",
                     "Встречи-архив/2026-07-20 Витрина/Вопросы и ответы.md",
                     "Встречи-архив/2026-07-20 Витрина/Черновик (live).md",
                     "Документация/2026-07-20_1000_hints.md"] {
            XCTAssertLessThan(ArchiveSearch.roleWeightForTests(path), 1.0,
                              "сырьё не демпфировано: \(path)")
        }
    }

    func testDistilledIsPreferred() {
        for path in ["Встречи/2026-07-20 Витрина_minutes.md",
                     "Встречи/2026-07-20 Витрина минутки.md",
                     "Ядра/Пилот витрины.md",
                     "Встречи-архив/2026-07-20 Витрина/Саммари.md",
                     "Документация/2026-07-20_разбор.md"] {
            XCTAssertGreaterThan(ArchiveSearch.roleWeightForTests(path), 1.0,
                                 "дистиллят не получил приоритета: \(path)")
        }
    }

    func testOrdinaryNotesStayNeutral() {
        XCTAssertEqual(ArchiveSearch.roleWeightForTests("Люди/Дмитрий.md"), 1.0)
        XCTAssertEqual(ArchiveSearch.roleWeightForTests("Системы/Витрина.md"), 1.0)
    }

    func testDistilledOutweighsRaw() {
        let minutes = ArchiveSearch.roleWeightForTests("Встречи/Витрина_minutes.md")
        let raw = ArchiveSearch.roleWeightForTests("Архив/Подсказки и ответы.md")
        XCTAssertGreaterThan(minutes / raw, 1.5,
                             "разрыв между выжимкой и сырьём слишком мал, чтобы влиять на порядок")
    }
}
