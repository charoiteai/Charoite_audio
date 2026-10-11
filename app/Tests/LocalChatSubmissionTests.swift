import XCTest
@testable import CharoiteApp

#if os(macOS)
@MainActor
final class LocalChatSubmissionTests: XCTestCase {
    private func withHistory(_ body: (LocalChatService, URL, Data) throws -> Void) throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let url = dir.appendingPathComponent("history.json")
        let initial = [LocalChatService.Message(role: "user", text: "Сохранённый вопрос")]
        let bytes = try JSONEncoder().encode(initial)
        try bytes.write(to: url)
        try body(LocalChatService(historyURL: url), url, bytes)
    }

    func testRejectedSubmissionDuringStreamingPreservesDraftAndHistory() throws {
        try withHistory { service, url, bytes in
            service.isStreaming = true
            let before = service.messages
            var draft = "Новый вопрос\nс уточнением"
            // Same acceptance contract used by the view; the refused send
            // never starts network work and must not discard the next question.
            if service.send(draft) { draft = "" }
            XCTAssertEqual(draft, "Новый вопрос\nс уточнением")
            XCTAssertEqual(service.messages, before)
            XCTAssertTrue(service.isStreaming)
            XCTAssertEqual(try Data(contentsOf: url), bytes)
        }
    }

    func testWhitespaceSubmissionDoesNotCreateMessagesOrStartStreaming() throws {
        try withHistory { service, url, bytes in
            let before = service.messages
            XCTAssertFalse(service.send(" \n\t "))
            XCTAssertEqual(service.messages, before)
            XCTAssertFalse(service.isStreaming)
            XCTAssertEqual(try Data(contentsOf: url), bytes)
        }
    }
}
#endif
