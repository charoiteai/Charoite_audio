import XCTest
@testable import CharoiteApp

#if os(macOS)
final class LibrarySearchReadinessTests: XCTestCase {
    func testMissingSettingIsNotANothingFoundResult() {
        XCTAssertEqual(LibrarySearchReadiness.resolve(graphDir: nil), .notConfigured)
    }

    func testConfiguredFolderIsReadyWithoutTouchingTheDisk() {
        let url = URL(fileURLWithPath: "/Volumes/asleep/graph")
        XCTAssertEqual(LibrarySearchReadiness.resolve(graphDir: url), .ready(url))
    }
}
#endif
