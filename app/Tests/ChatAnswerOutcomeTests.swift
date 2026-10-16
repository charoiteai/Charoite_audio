import XCTest
@testable import CharoiteApp

final class ChatAnswerOutcomeTests: XCTestCase {
    func testDoneWinsOverLateStopAndErrors() {
        XCTAssertEqual(ChatAnswerOutcome.decide(sawDone: true, cancelled: true, failed: false), .complete)
        XCTAssertEqual(ChatAnswerOutcome.decide(sawDone: true, cancelled: false, failed: true), .complete)
    }

    func testWithoutDoneTheCauseDecides() {
        XCTAssertEqual(ChatAnswerOutcome.decide(sawDone: false, cancelled: true, failed: true), .stopped)
        XCTAssertEqual(ChatAnswerOutcome.decide(sawDone: false, cancelled: false, failed: true), .failed)
        XCTAssertEqual(ChatAnswerOutcome.decide(sawDone: false, cancelled: false, failed: false), .dropped)
    }

    func testEveryNonEmptyAnswerWithoutDoneIsInterrupted() {
        for outcome in [ChatAnswerOutcome.stopped, .dropped, .failed] {
            XCTAssertTrue(outcome.marksInterrupted(textIsEmpty: false), "\(outcome)")
            XCTAssertFalse(outcome.marksInterrupted(textIsEmpty: true), "\(outcome)")
        }
        XCTAssertFalse(ChatAnswerOutcome.complete.marksInterrupted(textIsEmpty: false))
    }

    func testLengthCapIsATruncatedAnswerNotAComplete() {
        // num_predict исчерпан: тот же done, причина — в done_reason (DS, круг 4 по #804)
        let cut = ChatAnswerOutcome.decide(sawDone: true, doneReason: "length", cancelled: false, failed: false)
        XCTAssertEqual(cut, .truncated)
        XCTAssertTrue(cut.marksInterrupted(textIsEmpty: false))
        XCTAssertTrue(cut.getsMeta(textIsEmpty: false))
        XCTAssertEqual(ChatAnswerOutcome.decide(sawDone: true, doneReason: "stop", cancelled: false, failed: false), .complete)
    }

    func testStreamErrorCarriesTheServerReason() {
        XCTAssertEqual(ChatStreamError(message: "model runner has unexpectedly stopped").localizedDescription,
                       "model runner has unexpectedly stopped")
    }

    func testMetaDescribesOnlyArrivedText() {
        XCTAssertTrue(ChatAnswerOutcome.complete.getsMeta(textIsEmpty: true))
        XCTAssertTrue(ChatAnswerOutcome.dropped.getsMeta(textIsEmpty: false))
        XCTAssertFalse(ChatAnswerOutcome.dropped.getsMeta(textIsEmpty: true))
        XCTAssertFalse(ChatAnswerOutcome.stopped.getsMeta(textIsEmpty: false))
        XCTAssertFalse(ChatAnswerOutcome.failed.getsMeta(textIsEmpty: false))
    }

    func testEmptyBubbleSurvivesRestartForEveryOutcomeButStop() {
        // Пустой пузырь без меты saveHistory не пишет: «…» на экране и пусто в файле (круг 5 по #804).
        for outcome in [ChatAnswerOutcome.complete, .truncated, .stopped, .dropped, .failed] {
            let fate = outcome.emptyBubble(failure: "boom")
            switch outcome {
            case .stopped:
                XCTAssertEqual(fate, .remove)
            case .complete:
                XCTAssertEqual(fate, .keep)
                XCTAssertTrue(outcome.getsMeta(textIsEmpty: true), "мета держит пузырь в файле")
            case .truncated, .dropped, .failed:
                guard case .explain(let text) = fate else { return XCTFail("\(outcome): \(fate)") }
                XCTAssertFalse(text.isEmpty, "\(outcome)")
            }
        }
        guard case .explain(let text) = ChatAnswerOutcome.failed.emptyBubble(failure: "boom") else { return XCTFail() }
        XCTAssertTrue(text.contains("boom"))
    }

    func testModelRefusalDoesNotAskWhetherOllamaRuns() {
        let model = ChatAnswerOutcome.failureNote(ChatStreamError(message: "model runner has unexpectedly stopped"))
        XCTAssertTrue(model.contains("model runner has unexpectedly stopped"))
        XCTAssertFalse(model.contains("Ollama"), "отказ прислала сама Ollama")
        let transport = ChatAnswerOutcome.failureNote(URLError(.cannotConnectToHost))
        XCTAssertTrue(transport.contains("Ollama"))
    }

    func testHTTPRefusalCarriesServerReason() {
        // Ответ 404 живой Ollama: причина сервера, не «Ollama запущена?».
        let reason = LocalChatService.serverReason(body: #"{"error":"model \"qwen\" not found, try pulling it first"}"#)
        XCTAssertEqual(reason, #"model "qwen" not found, try pulling it first"#)
        XCTAssertNil(LocalChatService.serverReason(body: "<html>bad gateway</html>"))
        XCTAssertNil(LocalChatService.serverReason(body: #"{"error":"  "}"#))
        XCTAssertFalse(ChatAnswerOutcome.failureNote(ChatStreamError(message: reason ?? "")).contains("Ollama запущена"))
        // Прокси без причины: это не ошибка модели, и сервер модели живым не объявлен.
        let proxy: Error = ChatHTTPError(status: 502)
        XCTAssertTrue(ChatAnswerOutcome.failureNote(proxy).contains("502"))
        XCTAssertFalse(ChatAnswerOutcome.failureNote(proxy).contains("ошибка модели"))
    }

    func testServerLivenessFollowsWhoAnswered() {
        // Sonnet, DS, Haiku, круг 8: ответ HTTP без причины — не «Ollama не отвечает».
        XCTAssertEqual(ChatAnswerOutcome.serverAlive(after: ChatStreamError(message: "runner stopped")), true)
        XCTAssertNil(ChatAnswerOutcome.serverAlive(after: ChatHTTPError(status: 500)))
        XCTAssertEqual(ChatAnswerOutcome.serverAlive(after: URLError(.cannotConnectToHost)), false)
    }

    func testNoticeIsNotFedBackAsModelAnswer() {
        typealias M = LocalChatService.Message
        var notice = M(role: "assistant", text: "[ответ не пришёл]")
        notice.notice = true
        let history: [M] = [M(role: "user", text: "q1"), notice,
                            M(role: "user", text: "q2"), M(role: "assistant", text: "a2")]
        let entries = LocalChatService.contextHistory(history[...])
        let window = ChatContextWindow.select(history: entries, prompt: "q3")
        XCTAssertFalse(window.contains { $0.text == "[ответ не пришёл]" })
        XCTAssertEqual(window.map(\.text), ["q1", "q2", "a2", "q3"])
    }
}
