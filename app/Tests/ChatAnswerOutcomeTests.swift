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
}
