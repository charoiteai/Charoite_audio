import XCTest
@testable import CharoiteApp

/// Одно поручение — один пункт (№367). 23.09 каждое живое поручение стояло во
/// вкладке трижды — минутки, отчёт облачной ревизии и его исходник в
/// «Документации»: мост дописывает в минутки « (из ревизии)», и точная склейка
/// по тексту не срабатывала. Прячутся только копии пунктов минуток; поручение,
/// которого в минутках нет, остаётся на виду.
final class TasksCanonTests: XCTestCase {
    private var dir: URL!

    override func setUpWithError() throws {
        dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-task-canon-\(UUID().uuidString)")
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: dir)
    }

    private func write(_ rel: String, _ text: String) throws {
        let url = dir.appendingPathComponent(rel)
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try text.write(to: url, atomically: true, encoding: .utf8)
    }

    func testReviewCopiesOfMinutesItemAreHidden() throws {
        try write("Встречи-архив/2026-09-21 10-34 — Тема/Минутки.md",
                  "## Поручения\n- [ ] **Коля** — переформировать отчёт — до 22.09 (из ревизии)\n")
        try write("Встречи-архив/2026-09-21 10-34 — Тема/Ревизия.md",
                  "- [ ] **Коля** — переформировать отчёт — до 22.09\n")
        try write("Документация/Стенограммы встреч/2026-09-21_1034_Тема_ревизия.md",
                  "- [ ] **Коля** — переформировать отчёт — до 22.09\n")

        let items = TasksService.scanSync(graph: dir)

        XCTAssertEqual(items.map(\.text),
                       ["**Коля** — переформировать отчёт — до 22.09 (из ревизии)"],
                       "копии отчёта ревизии не должны дублировать пункт минуток")
        XCTAssertEqual(items.first?.file.lastPathComponent, "Минутки.md")
    }

    func testItemAbsentFromMinutesStaysVisible() throws {
        // Поручение, дописанное руками в заметку встречи, в минутках не значится —
        // прятать его нечем (DS и Sonnet, круг 1 по №367).
        try write("Встречи-архив/2026-09-21 10-34 — Тема/Минутки.md",
                  "- [ ] **Коля** — переформировать отчёт\n")
        try write("Встречи/2026-09-21_1034.md", "- [ ] **Аня** — позвонить в банк\n")
        try write("Встречи-архив/2026-09-21 10-34 — Тема/Ревизия.md",
                  "- [ ] **Саша** — не участник\n")
        try write("Документация/Стенограммы встреч/2026-09-21_1034_Тема_ревизия.md",
                  "- [ ] **Саша** — не участник\n")

        let texts = Set(TasksService.scanSync(graph: dir).map(\.text))

        XCTAssertEqual(texts, ["**Коля** — переформировать отчёт", "**Аня** — позвонить в банк",
                               "**Саша** — не участник"])
        XCTAssertEqual(TasksService.scanSync(graph: dir).count, 3, "копия «Саши» — одна строка")
    }

    func testWithdrawnMinutesItemDoesNotResurrectCopies() throws {
        try write("Встречи-архив/2026-09-01 10-00 — Старая/Минутки.md",
                  "- [-] **Коля** — давнее _(снято по сроку 23.09)_\n")
        try write("Встречи-архив/2026-09-01 10-00 — Старая/Ревизия.md",
                  "- [ ] **Коля** — давнее\n")

        let items = TasksService.scanSync(graph: dir)

        XCTAssertTrue(items.isEmpty, "получили: \(items.map(\.rel))")
        XCTAssertTrue(TasksService.meetingItems(items, for: "2026-09-01_1000").isEmpty)
    }

    func testReviewWithdrawnMinutesItemHidesCopies() throws {
        // Формат моста: снятое ревизией уезжает в «Снято ревизией» зачёркнутым, без
        // чекбокса; в минутках может не остаться ни одной строки «- [» (DS C1 круга 2).
        try write("Встречи-архив/2026-09-01 10-00 — Старая/Минутки.md",
                  "## Снято ревизией\n- ~~**Коля** — давнее~~ _(снято ревизией: нет в записи)_\n")
        try write("Встречи-архив/2026-09-01 10-00 — Старая/Ревизия.md", "- [ ] **Коля** — давнее\n")

        XCTAssertTrue(TasksService.scanSync(graph: dir).isEmpty)
    }

    func testOutsiderLineInMinutesHidesReviewCheckbox() throws {
        // №181: поручение не участнику — строка ⚠ без чекбокса; копия-чекбокс отчёта его не воскрешает.
        try write("Встречи-архив/2026-09-09 11-32 — План/Минутки.md",
                  "## Поручения\n- [ ] **Оля** — живое\n- ⚠ не участник (Саша): **Саша** — обзвонить хосты\n")
        try write("Встречи-архив/2026-09-09 11-32 — План/Ревизия.md", "- [ ] **Саша** — обзвонить хосты\n")

        XCTAssertEqual(TasksService.scanSync(graph: dir).map(\.text), ["**Оля** — живое"])
    }

    func testOnlyWriterFormatsCountAsKnown() throws {
        // «⚠ Риск срыва:» и зачёркивание без пометки ревизии — не форматы писателей;
        // чекбокс с теми же словами в заметке встречи остаётся (DS I2, круг 3).
        try write("Встречи-архив/2026-09-09 11-32 — План/Минутки.md",
                  "## Риски\n- ⚠ Риск срыва: **Саша** — обзвонить хосты\n- ~~Обсудили бюджет~~\n")
        try write("Встречи/2026-09-09_1132.md",
                  "- [ ] **Саша** — обзвонить хосты\n- [ ] Обсудили бюджет\n")

        XCTAssertEqual(TasksService.scanSync(graph: dir).count, 2)
    }

    func testPlusBulletOutsiderLineIsKnown() throws {
        try write("Встречи-архив/2026-09-09 11-32 — План/Минутки.md",
                  "+ ⚠ не участник (Саша): **Саша** — обзвонить хосты\n")
        try write("Встречи-архив/2026-09-09 11-32 — План/Ревизия.md", "- [ ] **Саша** — обзвонить хосты\n")

        XCTAssertTrue(TasksService.scanSync(graph: dir).isEmpty)
    }

    func testDiaryNoteWithMeetingDigitsIsNotDatedByMeeting() throws {
        // Дата встречи — только у файлов встречи; у личной заметки возраст — время файла.
        try write("Дневник/2020-01-01 10-00 — старьё.md", "- [ ] **Лена** — личное — до 15.03\n")

        let item = try XCTUnwrap(TasksService.scanSync(graph: dir).first)

        XCTAssertNil(item.dueAnchor)
        XCTAssertEqual(item.happenedAt, item.fileDate)
    }

    func testWordlessItemsAreNotGlued() throws {
        try write("Встречи-архив/2026-09-02 12-00 — Эмодзи/Минутки.md", "- [ ] ✅\n")
        try write("Встречи-архив/2026-09-02 12-00 — Эмодзи/Ревизия.md", "- [ ] 🔥\n- [ ] —\n")

        XCTAssertEqual(TasksService.scanSync(graph: dir).count, 3, "пустой ключ ни с чем не склеивается")
    }

    func testMeetingCardIgnoresDiaryNoteWithMeetingDigits() throws {
        try write("Встречи-архив/2026-09-21 10-34 — Тема/Минутки.md", "- [ ] **Коля** — отчёт\n")
        try write("Дневник/2026-09-21 10-34 — разговор.md", "- [ ] **Лена** — личное\n")

        let card = TasksService.meetingItems(TasksService.scanSync(graph: dir), for: "2026-09-21_1034")

        XCTAssertEqual(card.map(\.text), ["**Коля** — отчёт"])
    }

    func testMeetingWithoutMinutesKeepsOneCopyPreferringArchive() throws {
        try write("Документация/Стенограммы встреч/2026-08-04_1131_План_ревизия.md",
                  "- [ ] **Оля** — проверить сборку\n")
        try write("Встречи-архив/2026-08-04 11-31 — План/Ревизия.md",
                  "- [ ] **Оля** — проверить сборку\n")

        let items = TasksService.scanSync(graph: dir)

        XCTAssertEqual(items.count, 1)
        XCTAssertTrue(items[0].rel.hasPrefix("Встречи-архив/"), "осталась копия: \(items[0].rel)")
    }

    func testCopiesWithDifferentStateAreBothShown() throws {
        // Встреча без минуток: закрытая копия не прячется за открытой (DS I2).
        try write("Встречи-архив/2026-08-04 11-31 — План/Ревизия.md", "- [ ] **Оля** — проверить сборку\n")
        try write("Встречи/2026-08-04_1131.md", "- [x] **Оля** — проверить сборку\n")

        let items = TasksService.scanSync(graph: dir)

        XCTAssertEqual(items.map(\.done).sorted { !$0 && $1 }, [false, true])
    }

    func testNoteOutsideMeetingRootsIsNotGluedToMeeting() throws {
        // Личная заметка со штампом в имени даёт те же 12 цифр, но встречей не является (DS I4).
        try write("Встречи-архив/2026-09-21 10-34 — Тема/Минутки.md", "- [ ] **Лена** — личное\n")
        try write("Дневник/2026-09-21 10-34 — разговор.md", "- [ ] **Лена** — личное\n")

        XCTAssertEqual(TasksService.scanSync(graph: dir).count, 2)
    }

    func testUndatedNotesAreKeptAsIs() throws {
        try write("Задачи.md", "- [ ] **Лена** — личное\n- [ ] **Лена** — личное\n")

        XCTAssertEqual(TasksService.scanSync(graph: dir).count, 2)
    }

    func testSameTaskKeyMatchesBridgeKeyAndMarks() {
        // Случаи `_key` моста ревизии (src/review_bridge.py) и отметки снятия, которых мост не знает.
        let pairs = [
            ("**Коля** — отчёт — до 22.09 (из ревизии)", "**Коля** — отчёт — до 22.09"),
            ("⚠ не участник (Саша): **Саша** — написать", "**Саша** — написать"),
            ("Связаться с Марком, до пятницы", "связаться с Марком — до пятницы"),
            ("**Коля** — давнее _(снято по сроку 23.09)_", "**Коля** — давнее"),
            ("~~**Коля** — давнее~~ _(снято ревизией: нет в записи)_", "**Коля** — давнее"),
            ("~~**Kolya** — old~~ _(withdrawn by the review: absent)_", "**Kolya** — old"),
        ]
        for (a, b) in pairs {
            XCTAssertEqual(TasksService.sameTaskKey(a), TasksService.sameTaskKey(b), "\(a) ≠ \(b)")
        }
        XCTAssertNotEqual(TasksService.sameTaskKey("**Коля** — отчёт"),
                          TasksService.sameTaskKey("**Коля** — отчёт по КЗ"))
    }
}
