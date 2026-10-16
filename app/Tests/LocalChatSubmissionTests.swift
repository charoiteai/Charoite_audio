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
            // Exercise the actual view transition without constructing a window.
            // Reverting it to unconditional clearing makes this test fail.
            LocalChatView.submitDraft(&draft, using: service)
            XCTAssertEqual(draft, "Новый вопрос\nс уточнением")
            XCTAssertEqual(service.messages, before)
            XCTAssertTrue(service.isStreaming)
            XCTAssertEqual(try Data(contentsOf: url), bytes)
        }
    }

    func testStopPreservesVisibleQuestionBeforeRetrievalReturns() throws {
        try withHistory { service, url, _ in
            service.messages.append(.init(role: "user", text: "Новый вопрос до поиска"))
            service.messages.append(.init(role: "assistant", text: ""))
            service.isStreaming = true
            service.stopStreaming()
            XCTAssertFalse(service.isStreaming)
            let saved = try JSONDecoder().decode([LocalChatService.Message].self, from: Data(contentsOf: url))
            XCTAssertEqual(saved, service.messages)
            XCTAssertEqual(LocalChatService(historyURL: url).messages, service.messages)
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
