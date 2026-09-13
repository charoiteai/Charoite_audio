import XCTest
@testable import CharoiteApp

/// Аудит 13.09 (DS I2): «Повторить» для НЕ последней встречи ждало снимок через
/// latest() — более новая встреча его перекрывала, ожидание не закрывалось, через
/// три минуты UI объявлял конвейер молчащим и снова разрешал второй прогон поверх
/// работающего первого.
final class MeetingRetryExpectationTests: XCTestCase {
    private func snapshot(
        id: String,
        state: MeetingProcessingSnapshot.State,
        started: TimeInterval,
        updated: TimeInterval
    ) -> MeetingProcessingSnapshot {
        MeetingProcessingSnapshot(
            schemaVersion: 1,
            meetingID: id,
            state: state,
            stage: "complete",
            startedAt: started,
            updatedAt: updated,
            transcriptPath: "/transcripts/\(id).md",
            notePath: nil,
            error: nil,
            part: nil,
            parts: nil)
    }

    func testRetryOfAnOlderMeetingIsMatchedByMeetingIDNotByLatest() {
        let now = Date(timeIntervalSince1970: 2_000_000)
        let old = snapshot(id: "2026-09-12_1000", state: .error, started: 1_900_000, updated: 1_990_000)
        let fresh = snapshot(id: "2026-09-13_1000", state: .ready, started: 1_995_000, updated: 1_996_000)
        let retry = RetryExpectation(meetingID: old.meetingID, afterUpdatedAt: old.updatedAt,
                                     transcriptPath: old.transcriptPath)
        XCTAssertNil(MeetingProcessingPolicy.expected(in: [old, fresh], since: now, retry: retry, now: now),
                     "снимок повтора ещё не свежее того, что человек видел")
        let rerun = snapshot(id: old.meetingID, state: .processing, started: 1_900_000, updated: 1_999_000)
        XCTAssertEqual(
            MeetingProcessingPolicy.expected(in: [rerun, fresh], since: now, retry: retry, now: now)?.meetingID,
            old.meetingID, "повтор закрывается снимком своей встречи, хотя latest() — другая")
        XCTAssertEqual(MeetingProcessingPolicy.latest([rerun, fresh], now: now)?.meetingID, fresh.meetingID)
    }

    func testFailedRerunClosesTheExpectationToo() {
        // ошибка повторного прогона — честный результат, ожидание закрывается
        let now = Date(timeIntervalSince1970: 2_000_000)
        let old = snapshot(id: "2026-09-12_1000", state: .error, started: 1_900_000, updated: 1_990_000)
        let retry = RetryExpectation(meetingID: old.meetingID, afterUpdatedAt: old.updatedAt,
                                     transcriptPath: old.transcriptPath)
        let failedAgain = snapshot(id: old.meetingID, state: .error, started: 1_900_000, updated: 1_999_500)
        XCTAssertEqual(MeetingProcessingPolicy.expected(in: [failedAgain], since: now, retry: retry, now: now)?.state, .error)
    }

    func testStopExpectationStillUsesTheLatestStart() {
        let now = Date(timeIntervalSince1970: 2_000_000)
        let older = snapshot(id: "a", state: .ready, started: 1_999_000, updated: 1_999_000)
        let started = snapshot(id: "b", state: .processing, started: 2_000_001, updated: 2_000_001)
        XCTAssertEqual(MeetingProcessingPolicy.expected(in: [older, started], since: now, retry: nil, now: now)?.meetingID, "b")
        XCTAssertNil(MeetingProcessingPolicy.expected(in: [older], since: now, retry: nil, now: now),
                     "снимок раньше нажатия — не наш")
    }
}
