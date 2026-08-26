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

    private func snapshotAt(_ transcriptPath: String) -> MeetingProcessingSnapshot {
        MeetingProcessingSnapshot(
            schemaVersion: 1,
            meetingID: "2026-08-04_120310",
            state: .ready,
            stage: "complete",
            startedAt: 100,
            updatedAt: 100,
            transcriptPath: transcriptPath,
            notePath: nil,
            error: nil)
    }

    func testTitleDropsDerivedFileSuffixes() {
        // Инцидент 04.08: в старом статусе transcript_path указывал на файл
        // разбора — тема в списке была «Отчет по задачам разбор», а UI-rename
        // доклеивал хвост второй раз. Русские хвосты режутся так же, как
        // английские.
        XCTAssertEqual(
            snapshotAt("/transcripts/2026-08-04_1203_Отчет_по_задачам_разбор.md").title,
            "Отчет по задачам")
        XCTAssertEqual(
            snapshotAt("/transcripts/2026-08-04_1203_Тема_ревизия_claude.md").title,
            "Тема")
        XCTAssertEqual(
            snapshotAt("/transcripts/2026-07-31_1415_План.md").title,
            "План", "обычная тема не страдает")
    }

    func testStartedDateComesFromStampNotFromStatusWriteTime() {
        // started_at пишется конвейером после встречи: «Очистка ресурсов»
        // 11:31 показывалась «в 12:03». Начало встречи — это штамп.
        let snap = snapshotAt("/transcripts/2026-08-04_1131_Тема.md")
        let calendar = Calendar.current
        let parts = calendar.dateComponents(
            [.hour, .minute], from: snap.startedDate)
        XCTAssertEqual(parts.hour, 12)
        XCTAssertEqual(parts.minute, 3, "штамп 2026-08-04_120310 → 12:03")
    }

    func testEqualSnapshotStillPublishesWhenResolveFlips() {
        // Гейт «публикуем только изменившийся снимок» глотал переход
        // processing→error: файл статуса при зависшем конвейере не
        // меняется, а resolvedState меняет вердикт по времени (№433 C1).
        let processing = snapshot(state: .processing, updated: 100)
        let fresh = Date(timeIntervalSince1970: 100 + 60)
        let stale = Date(timeIntervalSince1970: 100 + 31 * 60)

        // свежий равный снимок с тем же резолвом — публикация не нужна
        XCTAssertFalse(MeetingProcessingPolicy.shouldPublish(
            current: processing, latest: processing,
            lastResolved: .processing, now: fresh))
        // тот же снимок, но резолв перещёлкнулся в error — публикуем
        XCTAssertTrue(MeetingProcessingPolicy.shouldPublish(
            current: processing, latest: processing,
            lastResolved: .processing, now: stale))
        // после публикации error-резолва повторов нет
        XCTAssertFalse(MeetingProcessingPolicy.shouldPublish(
            current: processing, latest: processing,
            lastResolved: .error, now: stale))
        // изменившийся снимок публикуется всегда
        XCTAssertTrue(MeetingProcessingPolicy.shouldPublish(
            current: processing, latest: snapshot(state: .ready),
            lastResolved: .processing, now: fresh))
        // пустой каталог после снимка — тоже событие
        XCTAssertTrue(MeetingProcessingPolicy.shouldPublish(
            current: processing, latest: nil,
            lastResolved: .processing, now: fresh))
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

    private func expectation(
        of snapshot: MeetingProcessingSnapshot
    ) -> RetryExpectation {
        RetryExpectation(
            meetingID: snapshot.meetingID,
            afterUpdatedAt: snapshot.updatedAt,
            transcriptPath: snapshot.transcriptPath)
    }

    func testRetryWaitsByFreshUpdateNotByStartTime() {
        // store сохраняет started_at первого запуска, поэтому статус повтора
        // приходит со «старым» started_at. Критерий «Стопа» его не увидел бы
        // и через три минуты объявил бы молчание про работающий конвейер.
        let pressed = Date(timeIntervalSince1970: 10_000)
        let seen = snapshot(state: .error, started: 100, updated: 9_998)
        let retryRun = snapshot(state: .processing, started: 100, updated: 10_002)

        XCTAssertFalse(
            MeetingProcessingPolicy.matchesExpectation(
                retryRun, since: pressed, retry: nil),
            "критерий „Стопа“ не видит повторный прогон — для того и отдельный")
        XCTAssertTrue(MeetingProcessingPolicy.matchesExpectation(
            retryRun, since: pressed, retry: expectation(of: seen)))

        // чужая встреча — не наш прогон
        let alien = RetryExpectation(
            meetingID: "2026-07-30_090001", afterUpdatedAt: 9_998, transcriptPath: "/t.md")
        XCTAssertFalse(MeetingProcessingPolicy.matchesExpectation(
            retryRun, since: pressed, retry: alien))
    }

    func testStatusTheUserClickedOnIsNotMistakenForTheRetryResult() {
        // Жать «Повторить» сразу, как появилась ошибка, — нормальное поведение.
        // Прежний допуск в пять секунд принимал ТОТ ЖЕ статус ошибки за
        // результат нового запуска: UI возвращался в «ошибка», кнопка
        // оживала, и следующее нажатие запускало второй конвейер поверх
        // работающего первого.
        let pressed = Date(timeIntervalSince1970: 10_000)
        let seen = snapshot(state: .error, started: 100, updated: 9_999)

        XCTAssertFalse(
            MeetingProcessingPolicy.matchesExpectation(
                seen, since: pressed, retry: expectation(of: seen)),
            "статус, по которому нажали кнопку, — прошлое, а не результат")

        // и лежалый статус той же встречи тоже не наш
        let stale = snapshot(state: .error, started: 100, updated: 9_000)
        XCTAssertFalse(MeetingProcessingPolicy.matchesExpectation(
            stale, since: pressed, retry: expectation(of: seen)))

        // а вот запись, сделанная конвейером после нажатия, — наша
        let fresh = snapshot(state: .processing, started: 100, updated: 10_001)
        XCTAssertTrue(MeetingProcessingPolicy.matchesExpectation(
            fresh, since: pressed, retry: expectation(of: seen)))
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
