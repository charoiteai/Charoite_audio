import XCTest
@testable import CharoiteApp

#if os(macOS)
/// Четыре глубины чтения карточки: сегмент показывает только то, что у
/// встречи есть, и каждая глубина знает свой файл для внешнего редактора.
final class MeetingCardDepthTests: XCTestCase {
    private func snapshot(note: String?, transcript: String) -> MeetingProcessingSnapshot {
        let json = """
        {"schema_version": 1, "meeting_id": "2026-08-04_11-20-00", "state": "ready",
         "stage": "done", "started_at": 0, "updated_at": 0,
         "transcript_path": "\(transcript)", "note_path": \(note.map { "\"\($0)\"" } ?? "null")}
        """
        return try! JSONDecoder().decode(MeetingProcessingSnapshot.self, from: Data(json.utf8))
    }

    func testOnlyExistingDepthsAreOffered() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("card-depth-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let transcript = dir.appendingPathComponent("t.md")
        try "текст".write(to: transcript, atomically: true, encoding: .utf8)

        var card = MeetingCard()
        let bare = MeetingCardDepth.available(
            card: card, meeting: snapshot(note: nil, transcript: transcript.path))
        XCTAssertEqual(bare, [.summary, .transcript], "без минуток и заметки — две глубины")

        card.minutes = MeetingMinutes(topics: [.init(text: "тема", level: 0)])
        let note = dir.appendingPathComponent("n.md")
        try "# заметка".write(to: note, atomically: true, encoding: .utf8)
        let full = MeetingCardDepth.available(
            card: card, meeting: snapshot(note: note.path, transcript: transcript.path))
        XCTAssertEqual(full, MeetingCardDepth.allCases)

        let missing = MeetingCardDepth.available(
            card: card, meeting: snapshot(note: note.path, transcript: dir.appendingPathComponent("нет.md").path))
        XCTAssertFalse(missing.contains(.transcript), "стенограммы на диске нет — глубины нет")
    }

    func testEachDepthOpensItsOwnFile() {
        var card = MeetingCard()
        card.archiveFolder = URL(fileURLWithPath: "/tmp/graph/Встречи-архив/2026-08-04 11-20")
        let meeting = snapshot(note: "/tmp/graph/Встречи/2026-08-04_1120.md", transcript: "/tmp/t.md")
        XCTAssertEqual(MeetingCardDepth.summary.file(card: card, meeting: meeting)?.lastPathComponent, "Саммари.md")
        XCTAssertEqual(MeetingCardDepth.minutes.file(card: card, meeting: meeting)?.lastPathComponent, "Минутки.md")
        XCTAssertEqual(MeetingCardDepth.analysis.file(card: card, meeting: meeting)?.path, "/tmp/graph/Встречи/2026-08-04_1120.md")
        XCTAssertEqual(MeetingCardDepth.transcript.file(card: card, meeting: meeting)?.path, "/tmp/t.md")
        // без архива резюме ведёт в заметку, а не в никуда
        card.archiveFolder = nil
        XCTAssertEqual(MeetingCardDepth.summary.file(card: card, meeting: meeting)?.path, "/tmp/graph/Встречи/2026-08-04_1120.md")
    }
}
#endif
