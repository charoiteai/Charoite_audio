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

    func testAsyncSearchReturnsTheSameArchiveResults() async {
        let hits = await MeetingSearch.searchAsync("ретеншн", graph: graph)
        XCTAssertTrue(hits.contains { $0.title.contains("Ретеншн партиций") })
    }

    /// Недоступная папка — nil, а не пустая выдача: экран архива называет
    /// причину вместо «ничего не найдено» (DS и Haiku, круг 4 по #781).
    func testLibrarySearchTellsMissingFolderFromNoHits() async throws {
        let missing = graph.appendingPathComponent("нет-такой-папки")
        let missingResult = await MeetingSearch.searchLibraryAsync("ретеншн", graph: missing)
        XCTAssertNil(missingResult)
        let file = graph.appendingPathComponent("файл-вместо-папки.md")
        try "x".write(to: file, atomically: true, encoding: .utf8)
        let fileResult = await MeetingSearch.searchLibraryAsync("ретеншн", graph: file)
        XCTAssertNil(fileResult)
        let none = await MeetingSearch.searchLibraryAsync("словокоторогонет", graph: graph)
        XCTAssertEqual(none?.isEmpty, true)
        let missingLegacy = await MeetingSearch.searchAsync("ретеншн", graph: missing)
        XCTAssertTrue(missingLegacy.isEmpty, "старая обёртка для Prep — пустота, как прежде")
    }

    func testUnreadableGraphFolderIsNotNoHits() async throws {
        let locked = graph.appendingPathComponent("закрытая")
        try FileManager.default.createDirectory(at: locked, withIntermediateDirectories: true)
        try FileManager.default.setAttributes([.posixPermissions: 0o000], ofItemAtPath: locked.path)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: locked.path) }
        let result = await MeetingSearch.searchLibraryAsync("ретеншн", graph: locked)
        XCTAssertNil(result, "нет доступа — причина, а не «ничего не найдено»")
    }

    /// Поиск открывает подпапки по имени: закрытый архив при открытом корне — тоже причина (DS, круг 6).
    func testUnreadableArchiveUnderReadableGraphIsNotNoHits() async throws {
        let root = graph.appendingPathComponent("граф-с-закрытым-архивом")
        let archive = root.appendingPathComponent("Встречи-архив/2026-08-01 10-00 — Тема")
        try FileManager.default.createDirectory(at: archive, withIntermediateDirectories: true)
        try "ретеншн".write(to: archive.appendingPathComponent("Саммари.md"), atomically: true, encoding: .utf8)
        let locked = root.appendingPathComponent("Встречи-архив").path
        try FileManager.default.setAttributes([.posixPermissions: 0o000], ofItemAtPath: locked)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: locked) }
        let result = await MeetingSearch.searchLibraryAsync("ретеншн", graph: root)
        XCTAssertNil(result)
    }

    /// Корень с правом прохода без чтения (`chmod 111`, общие тома): поиск его не листает и работает.
    func testTraversableButUnlistableGraphStillSearches() async throws {
        let root = graph.appendingPathComponent("граф-только-проход")
        let archive = root.appendingPathComponent("Встречи-архив/2026-08-01 10-00 — Тема")
        try FileManager.default.createDirectory(at: archive, withIntermediateDirectories: true)
        try "ретеншн".write(to: archive.appendingPathComponent("Саммари.md"), atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o111], ofItemAtPath: root.path)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: root.path) }
        let result = await MeetingSearch.searchLibraryAsync("ретеншн", graph: root)
        XCTAssertEqual(result?.isEmpty, false, "\(String(describing: result))")
    }

    /// Обходы идут по одному: следующий ждёт предыдущий и отдаёт свой результат.
    func testSequentialSearchesThroughTheGateBothAnswer() async throws {
        async let first = MeetingSearch.searchLibraryAsync("ретеншн", graph: graph)
        async let second = MeetingSearch.searchLibraryAsync("нет-такого-слова", graph: graph)
        let (a, b) = await (first, second)
        XCTAssertNotNil(a)
        XCTAssertEqual(b, [])
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
