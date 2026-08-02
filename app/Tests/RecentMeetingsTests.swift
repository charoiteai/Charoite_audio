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
            error: state == .error ? "graph_updater завершился с кодом 1" : nil)
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
}
