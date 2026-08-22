import Foundation
import XCTest
@testable import CharoiteApp

/// Потолок бессрочного daemon.err.log (аудит 16.08, п.7): хвост остаётся,
/// гигабайты — нет. Зеркало tests/test_log_trim.py на стороне Python.
final class LogTrimTests: XCTestCase {
    private func tempLog(lines: Int) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("logtrim-\(UUID().uuidString).log")
        var body = Data()
        for i in 0..<lines { body.append(Data("строка \(String(format: "%05d", i))\n".utf8)) }
        try body.write(to: url)
        return url
    }

    func testSmallLogIsLeftAlone() throws {
        let url = try tempLog(lines: 10)
        defer { try? FileManager.default.removeItem(at: url) }
        let before = try Data(contentsOf: url)
        XCTAssertFalse(LogTrim.trim(url, maxBytes: 10_000, keepBytes: 1_000))
        XCTAssertEqual(try Data(contentsOf: url), before)
    }

    func testBigLogKeepsTailOnLineBoundary() throws {
        let url = try tempLog(lines: 2_000)
        defer { try? FileManager.default.removeItem(at: url) }
        let size = try Data(contentsOf: url).count
        XCTAssertTrue(LogTrim.trim(url, maxBytes: size / 2, keepBytes: size / 10))
        let body = try String(contentsOf: url, encoding: .utf8)
        let lines = body.split(separator: "\n", omittingEmptySubsequences: false)
        XCTAssertTrue(lines[0].hasPrefix("[лог усечён при старте: было"))
        XCTAssertTrue(lines[1].hasPrefix("строка "), "первая строка хвоста — не обрывок: \(lines[1])")
        XCTAssertEqual(lines[lines.count - 2], "строка 01999")
        XCTAssertLessThanOrEqual(body.utf8.count, size / 10 + 80)
    }

    func testMissingLogIsNotAnError() {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("logtrim-missing-\(UUID().uuidString).log")
        XCTAssertFalse(LogTrim.trim(url))
    }
}
