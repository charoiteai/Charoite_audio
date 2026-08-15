import XCTest
@testable import CharoiteApp

/// Golden-векторы стеммера, общие с Python-реализацией (ревью 15.08).
///
/// Сверка разговора с узлами графа живёт в двух мирах: демон (Python,
/// src/graph_nodes.py) и приложение (Swift, ArchiveSearch.stem). Таблицы
/// окончаний обязаны давать одинаковые стемы — иначе «платёжный» находится
/// в приложении и молча теряется в демоне. Файл tests/stem_golden.json —
/// один на обе реализации; разъехались — падают оба теста.
final class StemGoldenTests: XCTestCase {
    func testGoldenVectors() throws {
        let here = URL(fileURLWithPath: #filePath)
        let golden = here
            .deletingLastPathComponent()   // Tests
            .deletingLastPathComponent()   // app
            .deletingLastPathComponent()   // репозиторий
            .appendingPathComponent("tests/stem_golden.json")
        let data = try Data(contentsOf: golden)
        let vectors = try JSONDecoder().decode([String: String].self, from: data)
        XCTAssertGreaterThan(vectors.count, 10, "golden-файл пуст или не найден")
        for (word, expected) in vectors {
            XCTAssertEqual(ArchiveSearch.stem(word), expected,
                           "стем «\(word)» разошёлся с Python-реализацией")
        }
    }
}
