import XCTest
@testable import CharoiteApp

/// Бюджет контекста: сколько текста уходит модели и в каком порядке.
///
/// Окно в 32К токенов — не цель, в которую надо целиться. Внимание модели
/// проседает в середине контекста, а лишний источник вредит сильнее, чем
/// помогает. Раньше объём ограничивался слепым `prefix(5000)` у вызывающего:
/// первый источник мог занять сколько угодно, а последний обрезался на
/// полуслове до огрызка.
final class ContextBudgetTests: XCTestCase {
    private func hit(_ name: String, _ size: Int) -> ArchiveSearch.Hit {
        ArchiveSearch.Hit(score: 1, rel: name,
                          block: "• \(name)\n  " + String(repeating: "т", count: size))
    }

    func testNoSingleSourceEatsTheWholeBudget() {
        let huge = hit("Стенограмма.md", 50_000)
        let small1 = hit("Ядра/Оплата.md", 800)
        let small2 = hit("Люди/Дмитрий.md", 800)

        let packed = ArchiveSearch.packContext([huge, small1, small2], budget: 6000)

        XCTAssertTrue(packed.contains("Ядра/Оплата.md"),
                      "стенограмма вытеснила остальные источники — ответ на «что решили» "
                      + "почти всегда требует нескольких встреч")
        XCTAssertTrue(packed.contains("Люди/Дмитрий.md"))
        XCTAssertLessThanOrEqual(packed.count, 6600, "бюджет превышен: \(packed.count)")
    }

    func testBudgetIsRespected() {
        let hits = (1...10).map { hit("Файл\($0).md", 3000) }
        let packed = ArchiveSearch.packContext(hits, budget: 6000)
        XCTAssertLessThanOrEqual(packed.count, 6600, "выдача \(packed.count) знаков при бюджете 6000")
    }

    func testTinyLeftoverIsDroppedNotTruncatedToGibberish() {
        // Бюджет подобран так, чтобы после первого источника осталось меньше
        // трёхсот знаков: именно этот остаток и должен быть отброшен целиком.
        let big = hit("Большой.md", 5800)
        let tail = hit("Хвост.md", 3000)
        let packed = ArchiveSearch.packContext([big, tail], budget: 900)
        XCTAssertFalse(packed.contains("Хвост.md"),
                       "источнику досталось меньше 300 знаков — огрызок занимает место и путает")
    }

    func testStrongestSourcesGoToBothEdges() {
        let hits = (1...4).map { hit("Файл\($0).md", 500) }
        let packed = ArchiveSearch.packContext(hits, budget: 6000)
        let blocks = packed.components(separatedBy: "\n\n")
        XCTAssertTrue(blocks.first?.contains("Файл1.md") == true,
                      "лучший источник не в начале: \(blocks.first ?? "")")
        XCTAssertTrue(blocks.last?.contains("Файл2.md") == true,
                      "второй по силе не в конце — оба сильных должны попасть в края, "
                      + "потому что середина контекста проседает: \(blocks.last ?? "")")
    }

    func testShortListIsNotReordered() {
        let hits = [hit("А.md", 300), hit("Б.md", 300)]
        let packed = ArchiveSearch.packContext(hits, budget: 6000)
        let blocks = packed.components(separatedBy: "\n\n")
        XCTAssertTrue(blocks.first?.contains("А.md") == true)
        XCTAssertEqual(blocks.count, 2)
    }

    func testEmptyInputGivesEmptyOutput() {
        XCTAssertTrue(ArchiveSearch.packContext([], budget: 6000).isEmpty)
    }
}
