import XCTest
@testable import CharoiteApp

final class MeetingProcessingTests: XCTestCase {
    private func snapshot(
        state: MeetingProcessingSnapshot.State,
        started: TimeInterval = 100,
        updated: TimeInterval = 100,
        note: String? = "/graph/Встречи/2026-07-31_1415.md"
    ) -> MeetingProcessingSnapshot {
        MeetingProcessingSnapshot(
            schemaVersion: 1,
            meetingID: "2026-07-31_141501",
            state: state,
            stage: state == .processing ? "updating_graph" : "complete",
            startedAt: started,
            updatedAt: updated,
            transcriptPath: "/transcripts/2026-07-31_1415_План.md",
            notePath: note,
            error: state == .error ? "failure" : nil)
    }

    func testPythonStatusContractDecodes() throws {
        let json = #"{"schema_version":1,"meeting_id":"2026-07-31_141501","state":"processing","stage":"rebuilding_transcript","started_at":100,"updated_at":120,"transcript_path":"/tmp/meeting.md"}"#

        let decoded = try JSONDecoder().decode(
            MeetingProcessingSnapshot.self,
            from: Data(json.utf8))

        XCTAssertEqual(decoded.meetingID, "2026-07-31_141501")
        XCTAssertEqual(decoded.state, .processing)
        XCTAssertEqual(decoded.stage, "rebuilding_transcript")
        XCTAssertEqual(decoded.updatedAt, 120)
    }

    func testProcessingBecomesExplicitErrorAfterThirtyMinutes() {
        let processing = snapshot(state: .processing, updated: 100)

        XCTAssertEqual(
            MeetingProcessingPolicy.resolvedState(
                processing,
                now: Date(timeIntervalSince1970: 100 + 30 * 60 - 1)),
            .processing)
        XCTAssertEqual(
            MeetingProcessingPolicy.resolvedState(
                processing,
                now: Date(timeIntervalSince1970: 100 + 30 * 60 + 1)),
            .error)
    }

    func testReadyOpensNoteAndFailureOpensTranscript() {
        let ready = snapshot(state: .ready)
        let failed = snapshot(state: .error)

        XCTAssertEqual(MeetingProcessingPolicy.actionPath(for: ready), ready.notePath)
        XCTAssertEqual(MeetingProcessingPolicy.actionPath(for: failed), failed.transcriptPath)
    }

    func testLatestStatusSurvivesRestartButOldHistoryStaysHidden() {
        let old = snapshot(state: .ready, started: 10, updated: 10)
        let recent = snapshot(state: .processing, started: 90_000, updated: 90_000)
        let now = Date(timeIntervalSince1970: 90_100)

        XCTAssertEqual(MeetingProcessingPolicy.latest([old, recent], now: now), recent)
        XCTAssertNil(MeetingProcessingPolicy.latest([old], now: now))
    }

    func testRetryOfferedOnlyForFailuresWithLiveTranscript() {
        // Ошибка с живой стенограммой — повтор возможен. Без стенограммы
        // повторять нечего. Готовой встрече кнопка не положена вовсе.
        let failed = snapshot(state: .error)
        XCTAssertTrue(MeetingProcessingPolicy.canRetry(failed, transcriptExists: true))
        XCTAssertFalse(MeetingProcessingPolicy.canRetry(failed, transcriptExists: false))
        XCTAssertFalse(MeetingProcessingPolicy.canRetry(
            snapshot(state: .ready), transcriptExists: true))

        // Зависший processing для пользователя — та же ошибка: результата нет.
        let stuck = snapshot(state: .processing, updated: 100)
        XCTAssertTrue(MeetingProcessingPolicy.canRetry(
            stuck, transcriptExists: true,
            now: Date(timeIntervalSince1970: 100 + 31 * 60)))
        XCTAssertFalse(MeetingProcessingPolicy.canRetry(
            stuck, transcriptExists: true,
            now: Date(timeIntervalSince1970: 100 + 60)))
    }

    func testRetryWaitsByFreshUpdateNotByStartTime() {
        // store сохраняет started_at первого запуска, поэтому статус повтора
        // приходит со «старым» started_at. Критерий «Стопа» его не увидел бы
        // и через три минуты объявил бы молчание про работающий конвейер.
        let pressed = Date(timeIntervalSince1970: 10_000)
        let retryRun = snapshot(state: .processing, started: 100, updated: 10_002)

        XCTAssertFalse(
            MeetingProcessingPolicy.matchesExpectation(
                retryRun, since: pressed, retryOf: nil),
            "критерий „Стопа“ не видит повторный прогон — для того и отдельный")
        XCTAssertTrue(MeetingProcessingPolicy.matchesExpectation(
            retryRun, since: pressed, retryOf: retryRun.meetingID))

        // чужая встреча и лежалый статус той же встречи — не наш прогон
        XCTAssertFalse(MeetingProcessingPolicy.matchesExpectation(
            retryRun, since: pressed, retryOf: "2026-07-30_090001"))
        let staleSameMeeting = snapshot(state: .error, started: 100, updated: 9_000)
        XCTAssertFalse(MeetingProcessingPolicy.matchesExpectation(
            staleSameMeeting, since: pressed, retryOf: staleSameMeeting.meetingID))
    }

    func testRetryCommandRunsSamePipelineFromVenv() {
        let cmd = MeetingRetryCommand.build(
            root: URL(fileURLWithPath: "/repo"),
            transcriptPath: "/transcripts/2026-07-31_1415_План.md")

        XCTAssertEqual(cmd.exec.path, "/usr/bin/nice")
        XCTAssertEqual(cmd.args, [
            "-n", "10",
            "/repo/.venv/bin/python",
            "/repo/src/rebuild_transcript.py",
            "/transcripts/2026-07-31_1415_План.md",
        ])
        // лог — тот же файл, что у демонского запуска этой встречи
        XCTAssertEqual(cmd.log.path, "/repo/logs/graph_2026-07-31_1415.log")
    }

    func testSilentPipelineStopsClaimingLaunchAfterGracePeriod() {
        // «Запускаю обработку встречи…» — обещание. Если конвейер за разумное
        // время не записал ни одного статуса (диск, права, ранний выход до
        // первой записи), обещание обязано смениться честной ошибкой, а не
        // висеть вечным спиннером.
        let pressed = Date(timeIntervalSince1970: 1_000)

        XCTAssertFalse(MeetingProcessingPolicy.waitingExpired(
            since: pressed, now: pressed.addingTimeInterval(60)),
            "минута — конвейер ещё может стартовать")
        XCTAssertTrue(MeetingProcessingPolicy.waitingExpired(
            since: pressed, now: pressed.addingTimeInterval(4 * 60)),
            "четыре минуты тишины — статус уже не появится")
    }
}
