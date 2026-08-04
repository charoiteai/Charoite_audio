import XCTest
@testable import CharoiteApp

/// Список последних встреч: раньше приложение показывало ровно одну — ту,
/// что случилась последней. Вчерашняя ошибка исчезала с экрана в ту секунду,
/// когда начиналась новая запись, хотя статусы лежат на диске две недели.
final class RecentMeetingsTests: XCTestCase {
    private func snapshot(
        id: String,
        state: MeetingProcessingSnapshot.State = .ready,
        started: TimeInterval,
        updated: TimeInterval? = nil,
        transcript: String = "/transcripts/2026-07-31_1415_План_релиза.md"
    ) -> MeetingProcessingSnapshot {
        MeetingProcessingSnapshot(
            schemaVersion: 1,
            meetingID: id,
            state: state,
            stage: "complete",
            startedAt: started,
            updatedAt: updated ?? started,
            transcriptPath: transcript,
            notePath: "/graph/Встречи/\(id).md",
            error: state == .error ? "graph_updater завершился с кодом 1" : nil,
            part: nil,
            parts: nil)
    }

    func testNewestFirstAndOlderThanTwoWeeksDropOut() {
        let now = Date(timeIntervalSince1970: 1_000_000)
        let today = snapshot(id: "today", started: 999_000)
        let yesterday = snapshot(id: "yesterday", started: 900_000)
        let ancient = snapshot(id: "ancient", started: 1_000_000 - 15 * 86_400)

        let list = MeetingProcessingPolicy.history([yesterday, ancient, today], now: now)

        XCTAssertEqual(list.map(\.meetingID), ["today", "yesterday"],
                       "новая встреча первая, старше двух недель — не показываем")
    }

    func testOneMeetingAppearsOnceEvenWithSeveralStatusFiles() {
        // Повтор пишет в тот же статус, но на диске мог остаться след прошлого
        // прогона. Одна встреча в двух состояниях сразу — худшее, что может
        // увидеть человек в списке.
        let now = Date(timeIntervalSince1970: 1_000_000)
        let failed = snapshot(id: "meeting", state: .error, started: 990_000, updated: 990_100)
        let fixed = snapshot(id: "meeting", state: .ready, started: 990_000, updated: 995_000)

        let list = MeetingProcessingPolicy.history([failed, fixed], now: now)

        XCTAssertEqual(list.count, 1)
        XCTAssertEqual(list.first?.state, .ready, "показываем свежее состояние встречи")
    }

    func testListIsCappedSoItStaysAGlanceNotAnArchive() {
        let now = Date(timeIntervalSince1970: 1_000_000)
        let many = (0..<40).map {
            snapshot(id: "m\($0)", started: 900_000 + TimeInterval($0))
        }

        let list = MeetingProcessingPolicy.history(many, now: now)

        XCTAssertEqual(list.count, MeetingProcessingPolicy.historyLimit)
        XCTAssertEqual(list.first?.meetingID, "m39", "срезаем хвост, а не голову")
    }

    func testTitleComesFromTheRenamedTranscript() {
        // graph_updater переименовывает файлы по теме разговора — тема уже в
        // имени, выдумывать и лезть в граф не нужно.
        let named = snapshot(id: "m", started: 1,
                             transcript: "/transcripts/2026-07-31_1415_План_релиза.md")
        XCTAssertEqual(named.title, "План релиза")

        // вспомогательные файлы не должны утаскивать суффикс в заголовок
        let minutes = snapshot(id: "m", started: 1,
                               transcript: "/transcripts/2026-07-31_1415_План_релиза_minutes.md")
        XCTAssertEqual(minutes.title, "План релиза")
    }

    func testUnnamedMeetingShowsItsDateInsteadOfAnEmptyLine() {
        // Пока разбор не дошёл до переименования, честнее показать дату.
        let raw = snapshot(id: "m", started: 1,
                           transcript: "/transcripts/2026-07-31_1415.md")

        XCTAssertFalse(raw.title.isEmpty)
        XCTAssertFalse(raw.title.contains("2026-07-31_1415"),
                       "штамп файла — не имя для человека")
    }

    func testStampWithSecondsIsNotMistakenForATitle() {
        // Живая запись даёт имя с секундами: 2026-08-03_113012.md. Заголовок
        // резал ровно 15 символов штампа — и от «…113012» оставалось «12».
        // В окне «Последние встречи» сегодняшняя встреча так и называлась:
        // числом. Поймано глазами, а не тестом: все прежние примеры были без
        // секунд.
        let live = snapshot(id: "m", started: 1,
                            transcript: "/transcripts/2026-08-03_113012.md")

        XCTAssertNotEqual(live.title, "12")
        XCTAssertFalse(live.title.allSatisfy(\.isNumber),
                       "имя встречи не может быть голым числом")
    }

    func testSecondsAreStrippedFromANamedMeetingToo() {
        let named = snapshot(id: "m", started: 1,
                             transcript: "/transcripts/2026-08-03_113012_План_релиза.md")
        XCTAssertEqual(named.title, "План релиза")
    }

    func testTitleStartingWithDigitsSurvives() {
        // «2026-07-31_1415_2026_год_планы» — после штампа идёт число, но это
        // уже часть темы: срезать две цифры можно только у секунд штампа.
        let named = snapshot(id: "m", started: 1,
                             transcript: "/transcripts/2026-07-31_1415_2026_год_планы.md")
        XCTAssertEqual(named.title, "2026 год планы")
    }
}

/// Состояния приходят из Python, и список у него длиннее, чем у нас.
final class MeetingStateDecodingTests: XCTestCase {
    private func decode(_ state: String) throws -> MeetingProcessingSnapshot {
        let json = """
        {"schema_version":1,"meeting_id":"m","state":"\(state)","stage":"s",
         "started_at":1,"updated_at":2,"transcript_path":"/t/2026-08-03_0844.md"}
        """
        return try JSONDecoder().decode(MeetingProcessingSnapshot.self,
                                        from: Data(json.utf8))
    }

    func testKnownStatesDecode() throws {
        XCTAssertEqual(try decode("ready").state, .ready)
        XCTAssertEqual(try decode("processing").state, .processing)
        XCTAssertEqual(try decode("error").state, .error)
    }

    func testRecordingWithoutSpeechIsItsOwnState() throws {
        // «в записи нет речи» — результат, а не авария: строгий разбор ошибки
        // и пустой записи в одно состояние заставлял человека искать поломку
        // там, где её нет
        XCTAssertEqual(try decode("empty").state, .empty)
    }

    func testStateFromANewerPipelineDoesNotDropTheMeeting() throws {
        // Строгий enum ронял разбор всего снимка, и встреча просто исчезала
        // из окна: совместимая правка на стороне Python превращалась в потерю
        // данных у того, кто ещё не обновил приложение.
        let snapshot = try decode("quarantined_by_a_future_version")

        XCTAssertEqual(snapshot.state, .unknown)
        XCTAssertEqual(snapshot.meetingID, "m", "остальные поля должны уцелеть")
    }
}

/// Что видно человеку, пока идёт обработка.
final class ProcessingProgressTests: XCTestCase {
    private func snapshot(stage: String, part: Int?, parts: Int?) -> MeetingProcessingSnapshot {
        MeetingProcessingSnapshot(
            schemaVersion: 1, meetingID: "m", state: .processing, stage: stage,
            startedAt: 1, updatedAt: 2,
            transcriptPath: "/t/2026-08-03_1130_Тема.md", notePath: nil, error: nil,
            part: part, parts: parts)
    }

    func testLongMeetingSaysWhichPartIsRunning() {
        // «Обновляю граф» на встрече в двадцать тысяч знаков висит минутами и
        // ничем не отличается от зависшего процесса.
        let text = MeetingProcessingPolicy.stageText(
            for: snapshot(stage: "updating_graph", part: 2, parts: 3))

        XCTAssertTrue(text.contains("2") && text.contains("3"), "часть и всего: \(text)")
    }

    func testShortMeetingDoesNotInventParts() {
        let text = MeetingProcessingPolicy.stageText(
            for: snapshot(stage: "updating_graph", part: nil, parts: nil))

        XCTAssertFalse(text.contains("часть"), "нечего делить — нечего и показывать: \(text)")
    }

    func testSinglePartIsNotAnnouncedEither() {
        // «часть 1 из 1» читается как начало долгого пути, а это уже финиш
        let text = MeetingProcessingPolicy.stageText(
            for: snapshot(stage: "updating_graph", part: 1, parts: 1))

        XCTAssertFalse(text.contains("часть"))
    }

    func testEarlyStagesStillSpeakHumanLanguage() {
        for stage in ["waiting_for_audio", "rebuilding_transcript", "неизвестная"] {
            let text = MeetingProcessingPolicy.stageText(
                for: snapshot(stage: stage, part: nil, parts: nil))
            XCTAssertFalse(text.isEmpty)
            XCTAssertFalse(text.contains(stage), "код стадии — не текст для человека")
        }
    }

    func testSilentRecordingOffersNoRetry() {
        // Тишину можно разбирать хоть трижды — речи в ней не появится.
        let empty = MeetingProcessingSnapshot(
            schemaVersion: 1, meetingID: "m", state: .empty, stage: "no_speech",
            startedAt: 1, updatedAt: 2, transcriptPath: "/t/2026-08-03_0844.md",
            notePath: nil, error: nil, part: nil, parts: nil)

        XCTAssertFalse(MeetingProcessingPolicy.canRetry(empty, transcriptExists: true))
    }
}

/// Что видно в строке списка, пока идёт повтор.
///
/// Пока одна встреча повторялась, погашенная кнопка «Повторить» вырастала у
/// ВСЕХ строк, включая готовые: список знал факт «идёт повтор», но не знал чей.
final class RetryControlTests: XCTestCase {
    private func snapshot(id: String, state: MeetingProcessingSnapshot.State) -> MeetingProcessingSnapshot {
        MeetingProcessingSnapshot(
            schemaVersion: 1, meetingID: id, state: state, stage: "s",
            startedAt: Date().timeIntervalSince1970 - 60,
            updatedAt: Date().timeIntervalSince1970,
            transcriptPath: "/t/\(id).md", notePath: nil, error: nil)
    }

    func testFailedMeetingOffersRetry() {
        XCTAssertEqual(MeetingProcessingPolicy.retryControl(
            for: snapshot(id: "a", state: .error),
            transcriptExists: true, retryingID: nil), .ready)
    }

    func testReadyMeetingShowsNothingEvenDuringSomeoneElsesRetry() {
        // главный случай из разбора: у готовой встречи повтору нет места
        XCTAssertEqual(MeetingProcessingPolicy.retryControl(
            for: snapshot(id: "ok", state: .ready),
            transcriptExists: true, retryingID: "b"), .hidden)
    }

    func testTheRetriedMeetingShowsProgressNotAButton() {
        // её статус уже переписан в processing — и всё равно «работаю», не кнопка
        XCTAssertEqual(MeetingProcessingPolicy.retryControl(
            for: snapshot(id: "b", state: .processing),
            transcriptExists: true, retryingID: "b"), .running)
    }

    func testOtherFailedMeetingWaitsItsTurn() {
        XCTAssertEqual(MeetingProcessingPolicy.retryControl(
            for: snapshot(id: "a", state: .error),
            transcriptExists: true, retryingID: "b"), .waiting)
    }

    func testNoTranscriptNoRetry() {
        XCTAssertEqual(MeetingProcessingPolicy.retryControl(
            for: snapshot(id: "a", state: .error),
            transcriptExists: false, retryingID: nil), .hidden)
    }
}

/// Кэш длительности: файл читается один раз на (встреча, updatedAt).
@MainActor
final class MeetingDurationCacheTests: XCTestCase {
    private var dir: URL!

    override func setUp() {
        super.setUp()
        MeetingDurationCache.reset()
        dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("duration-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: dir)
        super.tearDown()
    }

    private func meeting(updated: TimeInterval, path: String) -> MeetingProcessingSnapshot {
        MeetingProcessingSnapshot(
            schemaVersion: 1, meetingID: "m", state: .ready, stage: "complete",
            startedAt: 1, updatedAt: updated, transcriptPath: path,
            notePath: nil, error: nil, part: nil, parts: nil)
    }

    func testDurationComesFromTimecodes() throws {
        let t = dir.appendingPathComponent("a.md")
        try "**Ведущий [10:00:03]:** начало\n**Ведущий [10:47:12]:** конец\n"
            .write(to: t, atomically: true, encoding: .utf8)
        XCTAssertNotNil(MeetingDurationCache.durationText(for: meeting(updated: 5, path: t.path)))
    }

    func testSameStampIsCachedEvenIfFileChanges() throws {
        let t = dir.appendingPathComponent("a.md")
        try "**Ведущий [10:00:03]:** начало\n**Ведущий [10:47:12]:** конец\n"
            .write(to: t, atomically: true, encoding: .utf8)
        let first = MeetingDurationCache.durationText(for: meeting(updated: 5, path: t.path))
        try FileManager.default.removeItem(at: t)   // файл исчез — кэш не заметит
        XCTAssertEqual(MeetingDurationCache.durationText(for: meeting(updated: 5, path: t.path)),
                       first)
    }

    func testNewStampRereadsTheFile() throws {
        let t = dir.appendingPathComponent("a.md")
        try "**Ведущий [10:00:03]:** начало\n**Ведущий [10:47:12]:** конец\n"
            .write(to: t, atomically: true, encoding: .utf8)
        _ = MeetingDurationCache.durationText(for: meeting(updated: 5, path: t.path))
        try FileManager.default.removeItem(at: t)
        // доработка двинула updatedAt → честное перечтение (файла нет — nil)
        XCTAssertNil(MeetingDurationCache.durationText(for: meeting(updated: 6, path: t.path)))
    }
}
