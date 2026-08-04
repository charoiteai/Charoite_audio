import XCTest
@testable import CharoiteiOS

/// Парсинг графа: тема из заголовка встречи и человеческий штамп времени.
final class GraphParseTests: XCTestCase {
    func testTitleFromHeading() {
        let md = """
        ---
        type: встреча
        ---
        # Встреча 2026-07-27_1534 — Оптимизация бюджета текучки

        текст
        """
        XCTAssertEqual(GraphStore.title(from: md, fallback: "x"),
                       "Оптимизация бюджета текучки")
    }

    func testTitleFallsBackToFileName() {
        XCTAssertEqual(GraphStore.title(from: "просто текст без заголовка",
                                        fallback: "2026-07-27_1534"),
                       "2026-07-27_1534")
    }

    func testTitleWithoutDashKeepsWholeHeading() {
        XCTAssertEqual(GraphStore.title(from: "# Планёрка", fallback: "x"),
                       "Планёрка")
    }

    func testStamp() {
        XCTAssertEqual(GraphStore.stamp(from: "2026-07-27_1534"), "27.07 15:34")
        // неожиданное имя не ломает список — показываем как есть
        XCTAssertEqual(GraphStore.stamp(from: "черновик"), "черновик")
    }

    func testPortableMeetingManifest() throws {
        let json = """
        {"schema_version":1,"meeting_id":"2026-08-03_1130","title":"Planning",
         "duration_minutes":35,"participants":["Ivan"],"summary":"Done",
         "decisions":["Ship"],"action_items":["Test"],"open_questions":["When?"]}
        """
        let manifest = try XCTUnwrap(GraphStore.manifest(from: json))
        XCTAssertEqual(manifest.title, "Planning")
        XCTAssertEqual(manifest.actionItems, ["Test"])
        XCTAssertTrue(GraphStore.cardText(manifest).contains("Ship"))
    }
}
