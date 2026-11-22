import XCTest
import CryptoKit
@testable import CharoiteApp

#if os(macOS)
/// Четыре глубины чтения карточки: сегмент показывает только то, что у
/// встречи есть, и каждая глубина знает свой файл для внешнего редактора.
final class MeetingCardDepthTests: XCTestCase {
    private func snapshot(note: String?, transcript: String, id: String = "2026-08-04_11-20-00", updatedAt: Double = 0) -> MeetingProcessingSnapshot {
        let json = """
        {"schema_version": 1, "meeting_id": "\(id)", "state": "ready",
         "stage": "done", "started_at": 0, "updated_at": \(updatedAt),
         "transcript_path": "\(transcript)", "note_path": \(note.map { "\"\($0)\"" } ?? "null")}
        """
        return try! JSONDecoder().decode(MeetingProcessingSnapshot.self, from: Data(json.utf8))
    }

    func testPendingCardLoadNeverShowsAnotherSnapshotContent() {
        let first = snapshot(note: nil, transcript: "/tmp/first.md")
        let second = snapshot(note: nil, transcript: "/tmp/second.md", id: "2026-08-04_11-21-00")
        let refreshed = snapshot(note: nil, transcript: "/tmp/first.md", updatedAt: 1)
        var loaded = MeetingCard()
        loaded.gist = "First meeting only"
        loaded.participants = ["Synthetic speaker"]
        loaded.decisions = ["First decision"]
        loaded.tasks = ["First task"]
        loaded.openQuestions = ["First question"]
        loaded.minutes = MeetingMinutes(topics: [.init(text: "First topic", level: 0)])
        loaded.archiveFolder = URL(fileURLWithPath: "/tmp/first-archive")
        XCTAssertEqual(MeetingCardView.cardForSnapshot(loaded, loaded: first, current: first), loaded)
        for pending in [second, refreshed] {
            XCTAssertEqual(MeetingCardView.cardForSnapshot(loaded, loaded: first, current: pending), MeetingCard(),
                           "Every card field must be hidden until its exact snapshot is loaded")
        }
        XCTAssertEqual(MeetingCardView.cardForSnapshot(loaded, loaded: nil, current: first), MeetingCard())
        var preloaded = MeetingCard()
        preloaded.gist = "Current meeting only"
        XCTAssertEqual(MeetingCardView.cardForSnapshot(loaded, loaded: first, current: second, preloaded: preloaded), preloaded)
    }

    func testPendingResolutionDoesNotExposeLegacyNoteAsAnalysis() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("pending-debrief-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let note = dir.appendingPathComponent("graph.md")
        try "Legacy graph note".write(to: note, atomically: true, encoding: .utf8)
        let meeting = snapshot(note: note.path, transcript: dir.appendingPathComponent("t.md").path)
        let card = MeetingCard()
        XCTAssertNil(MeetingCardDepth.analysis.file(card: card, meeting: meeting, resolutionComplete: false))
        XCTAssertFalse(MeetingCardDepth.available(card: card, meeting: meeting, resolutionComplete: false).contains(.analysis))
        XCTAssertEqual(MeetingCardDepth.analysis.file(card: card, meeting: meeting, resolutionComplete: true), note)
        XCTAssertTrue(MeetingCardDepth.available(card: card, meeting: meeting, resolutionComplete: true).contains(.analysis))
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

    func testAnalysisOpensArchivedConversationWithAndWithoutGraphNote() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("card-debrief-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let debrief = dir.appendingPathComponent("Разбор.md")
        let note = dir.appendingPathComponent("note.md")
        try "Связный разговор".write(to: debrief, atomically: true, encoding: .utf8)
        try "Узлы графа".write(to: note, atomically: true, encoding: .utf8)
        let transcript = dir.appendingPathComponent("t.md")
        try "Full canonical speech".write(to: transcript, atomically: true, encoding: .utf8)
        try "Full canonical speech".write(to: dir.appendingPathComponent("Стенограмма.md"), atomically: true, encoding: .utf8)
        let direct = dir.appendingPathComponent("t_разбор.md")
        try "Связный разговор".write(to: direct, atomically: true, encoding: .utf8)
        try bind(transcript: transcript, debrief: direct)
        var card = MeetingCard()
        card.archiveFolder = dir
        for notePath in [nil, note.path] as [String?] {
            let meeting = snapshot(note: notePath, transcript: dir.appendingPathComponent("t.md").path)
            let verified = MeetingCardDepth.conversationFile(card: card, meeting: meeting)
            XCTAssertEqual(verified, debrief)
            XCTAssertTrue(MeetingCardDepth.available(card: card, meeting: meeting, conversation: verified).contains(.analysis))
            XCTAssertEqual(MeetingCardDepth.analysis.file(card: card, meeting: meeting, conversation: verified), debrief)
        }
        try FileManager.default.removeItem(at: debrief)
        try FileManager.default.removeItem(at: URL(fileURLWithPath: transcript.path + ".debrief-binding.json"))
        let legacy = snapshot(note: note.path, transcript: dir.appendingPathComponent("t.md").path)
        XCTAssertEqual(MeetingCardDepth.analysis.file(card: card, meeting: legacy), note)
        let absent = snapshot(note: nil, transcript: dir.appendingPathComponent("t.md").path)
        XCTAssertFalse(MeetingCardDepth.available(card: card, meeting: absent).contains(.analysis))
    }

    func testConversationRejectsAnotherArchiveAndVerifiesDirectPublication() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("exact-debrief-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let transcript = dir.appendingPathComponent("2026-01-01_120012.md")
        let sharedPrefix = "# Meeting\n\nA\nB\nC\n"
        let speech = sharedPrefix + "Second meeting speech"
        try speech.write(to: transcript, atomically: true, encoding: .utf8)
        try (sharedPrefix + "First meeting speech").write(to: dir.appendingPathComponent("Стенограмма.md"), atomically: true, encoding: .utf8)
        try "OTHER_MEETING".write(to: dir.appendingPathComponent("Разбор.md"), atomically: true, encoding: .utf8)
        let body = "Verified second conversation"
        let debrief = dir.appendingPathComponent("2026-01-01_120012_разбор.md")
        try body.write(to: debrief, atomically: true, encoding: .utf8)
        let hash: (String) -> String = { SHA256.hash(data: Data($0.utf8)).map { String(format: "%02x", $0) }.joined() }
        let binding = ["owner": transcript.lastPathComponent, "publication_id": "synthetic-publication",
                       "full_sha256": hash(speech), "debrief_file": debrief.lastPathComponent,
                       "debrief_sha256": hash(body)]
        let bound = URL(fileURLWithPath: transcript.path + ".debrief-binding.json")
        try JSONSerialization.data(withJSONObject: binding).write(to: bound)
        let meeting = snapshot(note: nil, transcript: transcript.path)
        var card = MeetingCard()
        card.archiveFolder = dir // same-minute archive selected by the legacy loader
        XCTAssertEqual(MeetingCardDepth.conversationFile(card: card, meeting: meeting), debrief)
        card.archiveFolder = nil // no graph/archive configured
        XCTAssertTrue(MeetingCardDepth.available(card: card, meeting: meeting, conversation: MeetingCardDepth.conversationFile(card: card, meeting: meeting)).contains(.analysis))
        XCTAssertEqual(MeetingCardDepth.conversationFile(card: card, meeting: meeting), debrief)
        let journal = URL(fileURLWithPath: transcript.path + ".debrief-publication.json")
        try Data("{}".utf8).write(to: journal)
        XCTAssertNil(MeetingCardDepth.conversationFile(card: card, meeting: meeting))
        try FileManager.default.removeItem(at: journal)
        try "Changed speech".write(to: transcript, atomically: true, encoding: .utf8)
        XCTAssertNil(MeetingCardDepth.conversationFile(card: card, meeting: meeting))
        try speech.write(to: transcript, atomically: true, encoding: .utf8)
        try "Changed debrief".write(to: debrief, atomically: true, encoding: .utf8)
        XCTAssertNil(MeetingCardDepth.conversationFile(card: card, meeting: meeting))
    }

    func testCurrentTranscriptDoesNotMakeStaleArchivedDebriefCurrent() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("stale-archive-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let transcript = dir.appendingPathComponent("t.md")
        let direct = dir.appendingPathComponent("t_разбор.md")
        let archived = dir.appendingPathComponent("Разбор.md")
        try "Current source".write(to: transcript, atomically: true, encoding: .utf8)
        try "Current source".write(to: dir.appendingPathComponent("Стенограмма.md"), atomically: true, encoding: .utf8)
        try "Fresh conversation".write(to: direct, atomically: true, encoding: .utf8)
        try "Old conversation left by excluded archive publication".write(to: archived, atomically: true, encoding: .utf8)
        var card = MeetingCard()
        card.archiveFolder = dir
        let meeting = snapshot(note: nil, transcript: transcript.path)
        XCTAssertNil(MeetingCardDepth.conversationFile(card: card, meeting: meeting), "An unbound archive is not a verified conversation")
        try bind(transcript: transcript, debrief: direct)
        XCTAssertEqual(MeetingCardDepth.conversationFile(card: card, meeting: meeting), direct)
        try Data("{}".utf8).write(to: URL(fileURLWithPath: transcript.path + ".debrief-publication.json"))
        XCTAssertNil(MeetingCardDepth.conversationFile(card: card, meeting: meeting), "Archive cannot bypass a pending publication")
    }

    private func bind(transcript: URL, debrief: URL) throws {
        func digest(_ bytes: Data) -> String { SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined() }
        let binding = ["owner": transcript.lastPathComponent, "publication_id": "synthetic-publication",
                       "full_sha256": digest(try Data(contentsOf: transcript)), "debrief_file": debrief.lastPathComponent,
                       "debrief_sha256": digest(try Data(contentsOf: debrief))]
        try JSONSerialization.data(withJSONObject: binding).write(
            to: URL(fileURLWithPath: transcript.path + ".debrief-binding.json"))
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

#if os(macOS)
extension MeetingCardDepthTests {
    /// Прежняя привычка «Подробно» переезжает в «Минутки» один раз и только
    /// если была выставлена явно; новый ключ не перетирается.
    func testDetailedPreferenceMigratesOnce() {
        let defaults = UserDefaults(suiteName: "card-depth-\(UUID().uuidString)")!
        MeetingCardView.migrateDepthPreference(defaults: defaults)
        XCTAssertNil(defaults.object(forKey: "meetingCardDepth"), "без старого ключа ничего не пишем")

        defaults.set(true, forKey: "meetingCardDetailed")
        MeetingCardView.migrateDepthPreference(defaults: defaults)
        XCTAssertEqual(defaults.string(forKey: "meetingCardDepth"), "minutes")

        defaults.set("transcript", forKey: "meetingCardDepth")
        MeetingCardView.migrateDepthPreference(defaults: defaults)
        XCTAssertEqual(defaults.string(forKey: "meetingCardDepth"), "transcript", "явный выбор не перетирается")
    }

    /// Источник — путь от графа, иначе от папки данных, иначе два звена.
    func testSourceLineIsRelativeToGraphThenRoot() {
        let graph = URL(fileURLWithPath: "/tmp/graph")
        let root = URL(fileURLWithPath: "/tmp/data")
        XCTAssertTrue(MeetingCardView.sourceLine(
            for: URL(fileURLWithPath: "/tmp/graph/Встречи/2026-08-04_1120.md"), graph: graph, root: root)
            .hasPrefix("Встречи/2026-08-04_1120.md"))
        XCTAssertTrue(MeetingCardView.sourceLine(
            for: URL(fileURLWithPath: "/tmp/data/transcripts/2026-08-04_11-20.md"), graph: graph, root: root)
            .hasPrefix("transcripts/2026-08-04_11-20.md"))
        XCTAssertTrue(MeetingCardView.sourceLine(
            for: URL(fileURLWithPath: "/elsewhere/a/b.md"), graph: nil, root: root)
            .hasPrefix("a/b.md"))
    }

    func testMinutesFollowTheFileWhenArchiveFolderKnown() throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("card-depth-live-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let transcript = dir.appendingPathComponent("t.md")
        try "текст".write(to: transcript, atomically: true, encoding: .utf8)
        let meeting = snapshot(note: nil, transcript: transcript.path)

        // Файл есть, кэш минуток пуст (появились после сборки записи) — глубина есть.
        var card = MeetingCard()
        card.archiveFolder = dir
        try "## Темы".write(to: dir.appendingPathComponent("Минутки.md"), atomically: true, encoding: .utf8)
        XCTAssertTrue(MeetingCardDepth.available(card: card, meeting: meeting).contains(.minutes),
                      "Минутки.md на диске — чип есть, даже если кэш ещё не перечитан")

        // Файл убрали (forget/rename), кэш ещё помнит минутки — глубины нет.
        try FileManager.default.removeItem(at: dir.appendingPathComponent("Минутки.md"))
        card.minutes = MeetingMinutes(topics: [.init(text: "тема", level: 0)])
        XCTAssertFalse(MeetingCardDepth.available(card: card, meeting: meeting).contains(.minutes),
                       "файла нет — устаревший кэш не делает чип кликабельным")
    }
}
#endif
