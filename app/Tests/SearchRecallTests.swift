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
