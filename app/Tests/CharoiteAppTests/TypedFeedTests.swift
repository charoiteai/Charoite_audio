import Foundation
import XCTest
@testable import CharoiteApp

final class TypedFeedTests: XCTestCase {
    @MainActor
    func testMalformedTopicMetadataFailsClosedAtomically() {
        let invalid: [[String: Any]] = [
            ["topic_id": "topic-a"], ["topic_label": "Topic A"],
            ["topic_id": "topic-a", "topic_label": ""],
            ["topic_id": "", "topic_label": "Topic A"],
            ["topic_id": " ", "topic_label": "Topic A"],
            ["topic_id": "topic-a", "topic_label": "\n"],
            ["topic_id": 7, "topic_label": "Topic A"],
            ["topic_id": "topic-a", "topic_label": false]
        ]
        for metadata in invalid {
            let service = SuflerService()
            var row: [String: Any] = ["id": "b1", "index": 0, "kind": "paragraph", "text": "Surface"]
            row.merge(metadata) { _, new in new }
            let data = try! JSONSerialization.data(withJSONObject: ["type": "thread", "blocks": [row]])
            service.consumeForTest(String(decoding: data, as: UTF8.self))
            XCTAssertTrue(service.feedProtocolFailed, "Accepted malformed metadata: \(metadata)")
            XCTAssertNil(service.feedBlocks)
        }
    }

    @MainActor
    func testAtomicEmptyMissingAndInvalidProjection() {
        let service = SuflerService()
        service.consumeForTest(#"{"type":"thread","live_feed":true,"text":"fallback","blocks":[]}"#)
        XCTAssertEqual(service.feedBlocks, [])
        XCTAssertFalse(service.feedProtocolFailed)
        service.consumeForTest(#"{"type":"thread","blocks":[{"id":"broken"}]}"#)
        XCTAssertTrue(service.feedProtocolFailed)
        XCTAssertNil(service.feedBlocks)
        service.consumeForTest(#"{"type":"thread","text":"classic"}"#)
        XCTAssertFalse(service.feedProtocolFailed)
        XCTAssertNil(service.feedBlocks)
        XCTAssertEqual(service.thread, "classic")
    }

    @MainActor
    func testManualHintRequiresExactCompletedPair() {
        let service = SuflerService()
        service.consumeForTest(#"{"type":"hint","manual":true,"manual_id":"m1","text":"answer"}"#)
        service.consumeForTest(Self.frame)
        service.isHinting = true
        XCTAssertFalse(service.hintIsInThread)
        service.consumeForTest(#"{"type":"hint_done","manual":true,"manual_id":"m1"}"#)
        XCTAssertTrue(service.hintIsInThread)
        service.consumeForTest(#"{"type":"hint","manual":true,"manual_id":"late","text":"other"}"#)
        XCTAssertFalse(service.hintIsInThread)
        service.consumeForTest(#"{"type":"hint_done","manual":true}"#)
        XCTAssertFalse(service.hintIsInThread)
    }

    @MainActor
    func testStableQuestionsAllowSameTextButRejectDuplicateIDsAndDanglingAnswers() {
        let service = SuflerService()
        service.consumeForTest(Self.frame)
        XCTAssertEqual(service.feedBlocks?.map(\.id), ["b1", "b2", "b3"])
        service.consumeForTest(Self.frame.replacingOccurrences(of: "q2", with: "q1"))
        XCTAssertTrue(service.feedProtocolFailed)
        service.consumeForTest(Self.frame.replacingOccurrences(of: #""answer_to":"q1""#, with: #""answer_to":"missing""#))
        XCTAssertTrue(service.feedProtocolFailed)
    }

    @MainActor
    func testInvalidAtomicIndexesFailClosed() {
        for invalid in [
            Self.frame.replacingOccurrences(of: #""index":1"#, with: #""index":0"#),
            Self.frame.replacingOccurrences(of: #""index":1"#, with: #""index":3"#),
            Self.frame.replacingOccurrences(of: #""index":0"#, with: #""index":-1"#),
            Self.frame.replacingOccurrences(of: #""index":0"#, with: #""index":2"#)
                .replacingOccurrences(of: #""index":2,"kind":"question","text":"Same?","question_id":"q2""#,
                                      with: #""index":0,"kind":"question","text":"Same?","question_id":"q2""#)
        ] {
            let service = SuflerService()
            service.consumeForTest(invalid)
            XCTAssertTrue(service.feedProtocolFailed)
            XCTAssertNil(service.feedBlocks)
        }
    }

    @MainActor
    func testManualScrollTargetComesFromTheExactLinkedPair() {
        let service = SuflerService()
        // Both questions carry the same manual ID; only the second has an answer.
        let frame = Self.frame
            .replacingOccurrences(of: #""answer_to":"q1""#, with: #""answer_to":"q2""#)
        let object = try! JSONSerialization.jsonObject(with: Data(frame.utf8)) as! [String: Any]
        var blocks = object["blocks"] as! [[String: Any]]
        blocks.swapAt(1, 2)
        blocks[1]["manual_id"] = "m1"
        for i in blocks.indices { blocks[i]["index"] = i }
        let data = try! JSONSerialization.data(withJSONObject: ["type": "thread", "blocks": blocks])
        service.consumeForTest(String(decoding: data, as: UTF8.self))
        service.consumeForTest(#"{"type":"hint_done","manual":true,"manual_id":"m1"}"#)
        XCTAssertTrue(service.hintIsInThread)
        XCTAssertEqual(service.correlatedManualQuestionID, "b3")
    }

    func testSameFrameManualPairTakesPriorityOverThreadFollow() {
        let previous = TypedFeedScrollUpdate(text: "before", blocks: [], manualQuestionID: nil)
        let update = TypedFeedScrollUpdate(text: "after", blocks: [], manualQuestionID: "b3")
        XCTAssertEqual(update.target(after: previous, follows: true), "b3")
        XCTAssertEqual(update.target(after: previous, follows: false), "b3")
        let append = TypedFeedScrollUpdate(text: "append", blocks: [], manualQuestionID: "b3")
        XCTAssertEqual(append.target(after: update, follows: true), "hintBottom")
        XCTAssertNil(append.target(after: update, follows: false))
    }

    static let frame = #"{"type":"thread","live_feed":true,"blocks":[{"id":"b1","index":0,"kind":"question","text":"Same?","question_id":"q1","manual_id":"m1"},{"id":"b2","index":1,"kind":"answer","text":"Answer","answer_to":"q1","manual_id":"m1"},{"id":"b3","index":2,"kind":"question","text":"Same?","question_id":"q2"}]}"#
}
