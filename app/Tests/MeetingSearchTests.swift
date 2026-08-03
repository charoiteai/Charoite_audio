import XCTest
@testable import CharoiteApp

/// Поиск по встречам: «где мы решали про X» без grep и Obsidian.
final class MeetingSearchTests: XCTestCase {
    private var graph: URL!

    override func setUpWithError() throws {
        graph = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
        let arch = graph.appendingPathComponent("Встречи-архив")
        for (folder, text) in [
            ("2026-08-01 10-00 — Ретеншн партиций",
             "## Решили\n- **Ретеншн** — хранить партиции 14 дней.\n"),
            ("2026-08-02 11-00 — Бюджет квартала",
             "## Решили\n- **Бюджет** — согласовать до пятницы.\n"),
        ] {
            let dir = arch.appendingPathComponent(folder)
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            try text.write(to: dir.appendingPathComponent("Саммари.md"),
                           atomically: true, encoding: .utf8)
        }
        let notes = graph.appendingPathComponent("Встречи")
        try FileManager.default.createDirectory(at: notes, withIntermediateDirectories: true)
        try "# Встреча — обсуждали ретеншн логов".write(
            to: notes.appendingPathComponent("2026-08-03_0900.md"),
            atomically: true, encoding: .utf8)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: graph)
        super.tearDown()
    }

    func testFindsTheMeetingByAWordFromItsSummary() {
        let hits = MeetingSearch.search("ретеншн", graph: graph)
        XCTAssertTrue(hits.contains { $0.title.contains("Ретеншн партиций") })
    }

    func testAllQueryWordsMustMatch() {
        // «бюджет ретеншн» вместе не встречаются нигде — пустой результат
        XCTAssertTrue(MeetingSearch.search("бюджет ретеншн", graph: graph).isEmpty)
    }

    func testCaseDoesNotMatter() {
        XCTAssertFalse(MeetingSearch.search("РЕТЕНШН", graph: graph).isEmpty)
    }

    func testGraphNotesAreSearchedForMeetingsWithoutArchive() {
        let hits = MeetingSearch.search("логов", graph: graph)
        XCTAssertTrue(hits.contains { $0.file.path.contains("Встречи/2026-08-03_0900") })
    }

    func testOneHitPerMeetingLeadsToTheFolder() {
        // одной находки на встречу достаточно: строка ведёт к папке целиком
        let hits = MeetingSearch.search("решили", graph: graph)
        XCTAssertEqual(hits.count, 2)
    }

    func testSnippetIsReadableNotMarkdown() {
        let hits = MeetingSearch.search("ретеншн", graph: graph)
        let s = hits.first?.snippet ?? ""
        XCTAssertFalse(s.contains("**"))
        XCTAssertFalse(s.hasPrefix("-"))
    }

    func testShortAndEmptyQueriesReturnNothing() {
        XCTAssertTrue(MeetingSearch.search("", graph: graph).isEmpty)
        XCTAssertTrue(MeetingSearch.search("а", graph: graph).isEmpty)
    }

    func testFreshMeetingsComeFirst() {
        let hits = MeetingSearch.search("решили", graph: graph)
        XCTAssertEqual(hits.first?.title.prefix(10), "2026-08-02")
    }
}

extension MeetingSearchTests {
    /// Архивная папка и заметка графа — одна встреча, а не две находки.
    ///
    /// Ключ дня у папки («2026-08-01 10-00 — Тема») и у заметки
    /// («2026-08-01_1000.md») пишется в разных форматах; дедуп, сравнивавший
    /// сырые префиксы, не совпадал никогда — встреча приходила дважды.
    func testArchivedMeetingIsNotDuplicatedByItsGraphNote() throws {
        let notes = graph.appendingPathComponent("Встречи")
        try "# Встреча 2026-08-01_1000 — Ретеншн партиций\nобсуждали ретеншн".write(
            to: notes.appendingPathComponent("2026-08-01_1000.md"),
            atomically: true, encoding: .utf8)

        let hits = MeetingSearch.search("ретеншн", graph: graph)
        let days = hits.map(\.day)
        XCTAssertEqual(days.count, Set(days).count,
                       "одна встреча пришла дважды: \(hits.map(\.title))")
        XCTAssertFalse(hits.contains { $0.file.path.hasSuffix("Встречи/2026-08-01_1000.md") },
                       "заметка графа продублировала архивную папку того же дня")
    }
}
