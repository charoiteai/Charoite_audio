import XCTest
@testable import CharoiteApp

final class ChatContextWindowTests: XCTestCase {
    private typealias Entry = ChatContextWindow.Entry

    func testLongCurrentQuestionIsIncludedWholeInsteadOfEmptyMessages() {
        let question = String(repeating: "вопрос", count: 2500)
        let output = ChatContextWindow.select(history: [], prompt: question)
        XCTAssertEqual(output, [Entry(role: "user", text: question)])
    }

    func testRecentTurnsFitAndOldTurnsDropAsWholePairs() {
        let history = [Entry(role: "user", text: "old"), Entry(role: "assistant", text: "answer"),
                       Entry(role: "user", text: "new"), Entry(role: "assistant", text: "reply")]
        let output = ChatContextWindow.select(history: history, prompt: "now", budget: 11)
        XCTAssertEqual(output.map(\.text), ["new", "reply", "now"])
        XCTAssertEqual(output.map(\.role), ["user", "assistant", "user"])
    }

    func testOversizedPriorAnswerDoesNotLeaveAnOrphanAssistant() {
        let history = [Entry(role: "user", text: "question"),
                       Entry(role: "assistant", text: String(repeating: "x", count: 50))]
        XCTAssertEqual(ChatContextWindow.select(history: history, prompt: "now", budget: 10).map(\.text), ["now"])
    }

    func testEmptyPlaceholdersAndOrphanHistoricalAnswersAreNotSent() {
        let history = [Entry(role: "assistant", text: "orphan"), Entry(role: "user", text: "previous"),
                       Entry(role: "assistant", text: ""), Entry(role: "system", text: "untrusted")]
        XCTAssertEqual(ChatContextWindow.select(history: history, prompt: "current").map(\.text), ["previous", "current"])
    }

    func testNegativeBudgetStillIncludesTheQuestion() {
        XCTAssertEqual(ChatContextWindow.select(history: [Entry(role: "user", text: "old")],
                                                prompt: "current", budget: -10).map(\.text), ["current"])
    }
}
