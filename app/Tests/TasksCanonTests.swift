import XCTest
@testable import CharoiteApp

/// Канон списка задач встречи (№367): у встречи с минутками поручения показывают
/// только они. 23.09 каждое живое поручение стояло во вкладке трижды — минутки,
/// отчёт облачной ревизии и его исходник в «Документации» (мост дописывает в
/// минутки « (из ревизии)», и точная склейка по тексту не срабатывала).
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

    func testMeetingWithMinutesShowsOnlyMinutes() throws {
        try write("Встречи-архив/2026-09-21 10-34 — Тема/Минутки.md",
                  "## Поручения\n- [ ] **Коля** — переформировать отчёт — до 22.09 (из ревизии)\n")
        try write("Встречи-архив/2026-09-21 10-34 — Тема/Ревизия.md",
                  "- [ ] **Коля** — переформировать отчёт — до 22.09\n- [ ] **Саша** — не участник\n")
        try write("Документация/Стенограммы встреч/2026-09-21_1034_Тема_ревизия.md",
                  "- [ ] **Коля** — переформировать отчёт — до 22.09\n- [ ] **Саша** — не участник\n")

        let items = TasksService.scanSync(graph: dir)

        XCTAssertEqual(items.map(\.text),
                       ["**Коля** — переформировать отчёт — до 22.09 (из ревизии)"],
                       "копии отчёта ревизии не должны дублировать пункт минуток")
        XCTAssertEqual(items.first?.file.lastPathComponent, "Минутки.md")
    }

    func testWithdrawnMinutesDoNotResurrectCopies() throws {
        // Минутки, где все пункты сняты «снято по сроку», — всё ещё канон:
        // вкладка и карточка не откатываются к чекбоксам отчёта ревизии.
        try write("Встречи-архив/2026-09-01 10-00 — Старая/Минутки.md",
                  "- [-] **Коля** — давнее _(снято по сроку 23.09)_\n")
        try write("Встречи-архив/2026-09-01 10-00 — Старая/Ревизия.md",
                  "- [ ] **Коля** — давнее\n")

        let items = TasksService.scanSync(graph: dir)

        XCTAssertTrue(items.isEmpty, "получили: \(items.map(\.rel))")
        XCTAssertTrue(TasksService.meetingItems(items, for: "2026-09-01_1000").isEmpty)
    }

    func testMinutesWithoutCheckboxesStillOwnTheMeeting() throws {
        try write("Встречи-архив/2026-09-02 12-00 — Прозой/Минутки.md",
                  "## Поручения\n- **Оля** — написать письмо\n")
        try write("Встречи-архив/2026-09-02 12-00 — Прозой/Ревизия.md",
                  "- [ ] **Оля** — написать письмо\n")

        XCTAssertTrue(TasksService.scanSync(graph: dir).isEmpty)
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

    func testUndatedNotesAreKeptAsIs() throws {
        try write("Задачи.md", "- [ ] **Лена** — личное\n- [ ] **Лена** — личное\n")

        XCTAssertEqual(TasksService.scanSync(graph: dir).count, 2)
    }
}
