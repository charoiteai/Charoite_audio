import XCTest
@testable import CharoiteApp

#if os(macOS)
final class LibrarySearchReadinessTests: XCTestCase {
    func testMissingSettingIsNotANothingFoundResult() {
        XCTAssertEqual(LibrarySearchReadiness.resolve(graphDir: nil, isDirectory: { _ in true }), .notConfigured)
    }

    func testConfiguredButMissingFolderIsUnreachable() {
        let url = URL(fileURLWithPath: "/nonexistent/graph")
        XCTAssertEqual(LibrarySearchReadiness.resolve(graphDir: url, isDirectory: { _ in false }), .unreachable(url))
    }

    func testRealDirectoryIsReadyAndAFileIsNot() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        XCTAssertEqual(LibrarySearchReadiness.resolve(graphDir: dir), .ready(dir))
        let file = dir.appendingPathComponent("graph.txt")
        try Data().write(to: file)
        XCTAssertEqual(LibrarySearchReadiness.resolve(graphDir: file), .unreachable(file))
    }
}
#endif
