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
}
