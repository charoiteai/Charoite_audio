import XCTest
@testable import CharoiteApp

#if os(macOS)
final class MeetingActionsTests: XCTestCase {
    func testProtocolUsesExactArchiveTimeAndNeverPassesTranscript() {
        let root = URL(fileURLWithPath: "/tmp/charoite")
        let graph = URL(fileURLWithPath: "/tmp/graph")
        let command = MeetingActionCommand.participantProtocol(
            root: root,
            meetingID: "2026-08-03_113012_Тема",
            graph: graph)

        XCTAssertEqual(command.executable.path, "/tmp/charoite/.venv/bin/python")
        XCTAssertEqual(command.arguments, [
            "/tmp/charoite/scripts/protocol.py",
            "2026-08-03 11-30",
            "--graph", "/tmp/graph",
            "--style", "plain",
        ])
        XCTAssertFalse(command.arguments.contains { $0.contains("transcripts") })
    }

    func testForgetIsDryRunUntilApplyIsExplicit() {
        let root = URL(fileURLWithPath: "/tmp/charoite")
        let graph = URL(fileURLWithPath: "/tmp/graph")
        let preview = MeetingActionCommand.forget(
            root: root, meetingID: "2026-08-03_113012", graph: graph, apply: false)
        let apply = MeetingActionCommand.forget(
            root: root, meetingID: "2026-08-03_113012", graph: graph, apply: true)

        XCTAssertFalse(preview.arguments.contains("--yes"))
        XCTAssertEqual(apply.arguments.last, "--yes")
        XCTAssertEqual(preview.arguments[1], "2026-08-03_113012")
    }

    /// Критика DS r3 по #499: посекундный ID уходит скрипту целиком —
    /// иначе соседку той же минуты из приложения не забыть, а «забыть» её
    /// стирало бы владельца минуты. Минутный ID (после наката темы) —
    /// как прежде, минута; суффикс коллизии — часть штампа.
    func testForgetTargetKeepsTheSecondsWhenTheIDHasThem() {
        XCTAssertEqual(MeetingActionCommand.forgetTarget("2026-08-03_113012"), "2026-08-03_113012")
        XCTAssertEqual(MeetingActionCommand.forgetTarget("2026-08-03_113012_Отчёт"), "2026-08-03_113012")
        XCTAssertEqual(MeetingActionCommand.forgetTarget("2026-08-03_113012-1"), "2026-08-03_113012-1")
        XCTAssertEqual(MeetingActionCommand.forgetTarget("2026-08-03_1130_Отчёт"), "2026-08-03_1130")
        XCTAssertEqual(MeetingActionCommand.forgetTarget("2026-08-03_1130"), "2026-08-03_1130")
    }

    /// Аудит 05.09: копия импортированной встречи живёт в папке импорта —
    /// «забыть» получает её путь от приложения, без папки флага нет.
    func testForgetPassesTheImportFolderWhenKnown() {
        let root = URL(fileURLWithPath: "/tmp/charoite")
        let graph = URL(fileURLWithPath: "/tmp/graph")
        let withFolder = MeetingActionCommand.forget(
            root: root, meetingID: "2026-08-03_113012", graph: graph, apply: true,
            importFolder: "~/Charoite_inbox")
        XCTAssertTrue(withFolder.arguments.contains("--import-folder"))
        let idx = withFolder.arguments.firstIndex(of: "--import-folder")!
        XCTAssertFalse(withFolder.arguments[idx + 1].hasPrefix("~"), "тильда раскрыта для скрипта")
        XCTAssertEqual(withFolder.arguments.last, "--yes")
        let without = MeetingActionCommand.forget(
            root: root, meetingID: "2026-08-03_113012", graph: graph, apply: false, importFolder: "")
        XCTAssertFalse(without.arguments.contains("--import-folder"))
    }
}
#endif
