import XCTest
@testable import CharoiteApp

/// Байтовый счётчик обязан считать ровно то же, что считал строковый.
final class ByteCountingTests: XCTestCase {
    func testCountsMatchNaiveStringSearch() {
        let cases: [(String, String)] = [
            ("витрин", "витрина витрины ВИТРИН витринах"),
            ("ё", "ёлка ёж"),
            ("аа", "аааа"),                     // без перекрытий: 2, а не 3
            ("нет", "здесь этого слова не найти"),
            ("", "пусто"),
            ("длинная игла", "короткий"),
        ]
        for (needle, hay) in cases {
            let naive = naiveCount(of: needle, in: hay)
            let fast = ArchiveSearch.countOccurrencesForTests(of: needle, in: hay)
            XCTAssertEqual(fast, naive, "«\(needle)» в «\(hay)»: байты \(fast), строки \(naive)")
        }
    }

    private func naiveCount(of needle: String, in hay: String) -> Int {
        guard !needle.isEmpty else { return 0 }
        var count = 0
        var idx = hay.startIndex
        while let r = hay.range(of: needle, range: idx..<hay.endIndex) {
            count += 1
            idx = r.upperBound
        }
        return count
    }
}
