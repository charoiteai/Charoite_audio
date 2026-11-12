import Foundation
import XCTest
@testable import CharoiteApp

final class TypedFeedTopicTests: XCTestCase {
    func testOversizedOrControlTopicMetadataIsRejected() {
        for invalid in [String(repeating: "a", count: 385), "Topic\nBreak", "Topic\tTab", "Topic\u{2028}Break", "Topic\u{200B}Hidden"] {
            let labelFrame: [[String: Any]] = [["id": "b1", "index": 0, "kind": "paragraph", "text": "Surface", "topic_id": "a", "topic_label": invalid]]
            XCTAssertNil(TypedFeedBlock.decode(labelFrame))
            let idFrame: [[String: Any]] = [["id": "b1", "index": 0, "kind": "paragraph", "text": "Surface", "topic_id": invalid, "topic_label": "Topic"]]
            XCTAssertNil(TypedFeedBlock.decode(idFrame))
        }
        let emojiLabel: [[String: Any]] = [["id": "b1", "index": 0, "kind": "paragraph", "text": "Surface", "topic_id": "a", "topic_label": String(repeating: "🌍", count: 257)]]
        XCTAssertNil(TypedFeedBlock.decode(emojiLabel))
    }
    static let fixture = #"""
    [
      {"id":"b1","index":0,"kind":"paragraph","text":"First  surface\nwith é and **markup**","topic_id":"a","topic_label":"Topic A"},
      {"id":"b2","index":1,"kind":"system","text":"Paused","topic_id":"","topic_label":""},
      {"id":"b3","index":2,"kind":"question","text":"Which scopes?","question_id":"q1","manual_id":"m1","topic_id":"a","topic_label":"Topic A"},
      {"id":"b4","index":3,"kind":"answer","text":"Scope one  only.","answer_to":"q1","manual_id":"m1","answer_id":"a1","scope_ids":["s1"],"status":"partial","topic_id":"a","topic_label":"Topic A"},
      {"id":"b5","index":4,"kind":"answer","text":"Scope two\nlater.","answer_to":"q1","manual_id":"m1","answer_id":"a2","scope_ids":["s2"],"status":"complete","topic_id":"a","topic_label":"Topic A"},
      {"id":"b6","index":5,"kind":"paragraph","text":"Second surface","topic_id":"b","topic_label":"Topic B"},
      {"id":"b7","index":6,"kind":"paragraph","text":"Return surface","topic_id":"a","topic_label":"Topic A"}
    ]
    """#

    private func decode(_ json: String = fixture) throws -> [TypedFeedBlock] {
        let object = try JSONSerialization.jsonObject(with: Data(json.utf8))
        return try XCTUnwrap(TypedFeedBlock.decode(object))
    }

    private func frame(_ json: String = fixture) throws -> String {
        let blocks = try JSONSerialization.jsonObject(with: Data(json.utf8))
        let data = try JSONSerialization.data(withJSONObject: ["type": "thread", "live_feed": true, "blocks": blocks])
        return String(decoding: data, as: UTF8.self)
    }

    func testTwoTopicsReturnAndSystemPreserveEverySurfaceAndBlockIdentity() throws {
        let blocks = try decode()
        XCTAssertEqual(blocks[0].topicID, "a")
        XCTAssertEqual(blocks[0].topicLabel, "Topic A")
        let rows = TypedFeedRow.project(blocks)
        XCTAssertEqual(rows.map(\.id), [
            .topic(beforeBlockID: "b1"), .block("b1"), .block("b2"), .block("b3"),
            .block("b4"), .block("b5"), .topic(beforeBlockID: "b6"), .block("b6"),
            .topic(beforeBlockID: "b7"), .block("b7")
        ])
        let labels = rows.compactMap { row -> String? in
            if case .topic(_, let label, _) = row { return label }
            return nil
        }
        XCTAssertEqual(labels, ["Topic A", "Topic B", "Topic A"])
        let displayed = rows.compactMap { row -> TypedFeedBlock? in
            if case .block(let block) = row { return block }
            return nil
        }
        XCTAssertEqual(displayed, blocks)
        XCTAssertEqual(Array(displayed[0].text.utf8), Array("First  surface\nwith é and **markup**".utf8))
        XCTAssertEqual(displayed.map { Array($0.text.utf8) }, blocks.map { Array($0.text.utf8) })
        let appended = try decode(Self.fixture.replacingOccurrences(of: "Return surface", with: "Return surface extended"))
        XCTAssertEqual(TypedFeedRow.project(appended).map(\.id), rows.map(\.id))
    }

    func testLegacyAndTopiclessManualFramesDoNotInventTitlesOrBreakPairs() throws {
        let json = #"[{"id":"hintBottom","index":0,"kind":"question","text":"Manual?","question_id":"q1","manual_id":"m1"},{"id":"topic:b1","index":1,"kind":"answer","text":"Manual answer","answer_to":"q1","manual_id":"m1","topic_id":"","topic_label":""},{"id":"b3","index":2,"kind":"system","text":"Resumed","topic_id":null,"topic_label":null}]"#
        let blocks = try decode(json)
        XCTAssertEqual(TypedFeedRow.project(blocks).map(\.id), blocks.map { .block($0.id) })
        let typed = try decode(Self.fixture.replacingOccurrences(of: #""id":"b1""#, with: #""id":"topic:b1""#))
        let rows = TypedFeedRow.project(typed)
        XCTAssertEqual(Set(rows.map(\.id)).count, rows.count)
        XCTAssertNotEqual(TypedFeedRow.ID.topic(beforeBlockID: "topic:b1"), .block("topic:b1"))
    }

    func testTopiclessManualPairDoesNotResetTheSurroundingTopic() throws {
        let blocks = try decode(#"[{"id":"p1","index":0,"kind":"paragraph","text":"Before","topic_id":"a","topic_label":"Topic A"},{"id":"q1","index":1,"kind":"question","text":"Manual?","question_id":"q1","manual_id":"m1","topic_id":"","topic_label":""},{"id":"a1","index":2,"kind":"answer","text":"Manual answer","answer_to":"q1","manual_id":"m1","topic_id":"","topic_label":""},{"id":"p2","index":3,"kind":"paragraph","text":"After","topic_id":"a","topic_label":"Topic A"}]"#)
        XCTAssertEqual(TypedFeedRow.project(blocks).map(\.id), [
            .topic(beforeBlockID: "p1"), .block("p1"), .block("q1"), .block("a1"), .block("p2")
        ])
    }

    @MainActor
    func testPartialAnswersAndTopicTitlesKeepManualCardCorrelationAndScrollTarget() throws {
        let service = SuflerService()
        service.consumeForTest(try frame())
        service.consumeForTest(#"{"type":"hint","manual":true,"manual_id":"m1","text":"Scope two\nlater."}"#)
        service.isHinting = true
        XCTAssertFalse(service.hintIsInThread)
        service.consumeForTest(#"{"type":"hint_done","manual":true,"manual_id":"m1"}"#)
        XCTAssertTrue(service.hintIsInThread)
        XCTAssertEqual(service.correlatedManualQuestionID, "b3")
        let rows = TypedFeedRow.project(try XCTUnwrap(service.feedBlocks))
        let question = try XCTUnwrap(rows.firstIndex { $0.id == .block("b3") })
        XCTAssertEqual(Array(rows[question...question + 2].map(\.id)), [.block("b3"), .block("b4"), .block("b5")])
        let previous = TypedFeedScrollUpdate(text: "before", blocks: [], manualQuestionID: nil)
        let update = TypedFeedScrollUpdate(text: "after", blocks: service.feedBlocks,
                                          manualQuestionID: service.correlatedManualQuestionID)
        XCTAssertEqual(update.target(after: previous, follows: false), "b3")
        let reader = MeetingFeedReader()
        XCTAssertFalse(reader.followsUpdate(manualCardOpen: true))
        reader.observe(.init(distanceFromBottom: 300, contentHeight: 1200), viewport: 400)
        XCTAssertFalse(reader.followsUpdate(manualCardOpen: false))
        XCTAssertNil(update.target(after: update, follows: reader.followsUpdate(manualCardOpen: false)))
    }

    @MainActor
    func testChangedLabelForeignAnswerAndThemedSystemFailClosed() throws {
        for invalid in [
            Self.fixture.replacingOccurrences(of: #""topic_id":"b","topic_label":"Topic B""#,
                                             with: #""topic_id":"a","topic_label":"Topic B""#),
            Self.fixture.replacingOccurrences(of: #""status":"partial","topic_id":"a","topic_label":"Topic A""#,
                                             with: #""status":"partial","topic_id":"b","topic_label":"Topic B""#),
            Self.fixture.replacingOccurrences(of: #""topic_id":"","topic_label":"""#,
                                             with: #""topic_id":"a","topic_label":"Topic A""#)
        ] {
            let service = SuflerService()
            service.consumeForTest(try frame(invalid))
            XCTAssertTrue(service.feedProtocolFailed)
            XCTAssertNil(service.feedBlocks)
        }
    }
}
