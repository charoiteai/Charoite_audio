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
        XCTAssertEqual(preview.arguments[1], "2026-08-03_1130")
    }
}
#endif
