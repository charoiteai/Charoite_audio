import XCTest
@testable import CharoiteApp

#if os(macOS)
final class SourceChipFlowTests: XCTestCase {
    func testNarrowPanelWrapsByWidthKeepingOrderAndEverySource() {
        let sizes = [CGSize(width: 90, height: 20), CGSize(width: 90, height: 20),
                     CGSize(width: 40, height: 20), CGSize(width: 60, height: 20)]
        let frames = SourceChipFlow.frames(sizes: sizes, width: 180)
        XCTAssertEqual(frames.count, sizes.count)
        XCTAssertEqual(frames.map(\.minY), [0, 24, 24, 48])
        XCTAssertTrue(frames.allSatisfy { $0.minX >= 0 && $0.maxX <= 180 })
    }

    func testWidePanelCanFitMoreThanThreeChipsOnOneRow() {
        let frames = SourceChipFlow.frames(sizes: Array(repeating: CGSize(width: 60, height: 20), count: 5), width: 320)
        XCTAssertEqual(frames.map(\.minY), [0, 0, 0, 0, 0])
        XCTAssertEqual(frames.last?.maxX, 316)
    }

    func testOversizedChipIsConstrainedAndFollowingChipMovesBelowIt() {
        let frames = SourceChipFlow.frames(sizes: [CGSize(width: 700, height: 24), CGSize(width: 50, height: 20)], width: 140)
        XCTAssertEqual(frames[0].width, 140)
        XCTAssertEqual(frames[1].minY, 28)
    }

    func testTallestChipDeterminesNextRowAndEmptySourcesNeedNoSpace() {
        let frames = SourceChipFlow.frames(sizes: [CGSize(width: 40, height: 20), CGSize(width: 40, height: 30),
                                                  CGSize(width: 60, height: 10)], width: 90)
        XCTAssertEqual(frames[2].minY, 34)
        XCTAssertTrue(SourceChipFlow.frames(sizes: [], width: 0).isEmpty)
    }
}
#endif
