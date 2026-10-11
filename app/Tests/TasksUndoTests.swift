import XCTest
@testable import CharoiteApp

@MainActor
final class TasksUndoTests: XCTestCase {
    private func withFile(_ text: String, _ body: (URL, URL) throws -> Void) throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("task-undo-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("Минутки.md")
        try text.write(to: file, atomically: true, encoding: .utf8)
        try body(root, file)
    }

    private func change(in root: URL) throws -> TasksService.Change {
        let item = try XCTUnwrap(TasksService.scanSync(graph: root).first)
        let outcome = TasksService.toggleRecordingSync(item)
        XCTAssertEqual(outcome.result, .changed)
        return try XCTUnwrap(outcome.change)
    }

    func testUndoRestoresOriginalBytesIncludingUppercaseMarkerAndCRLF() throws {
        let original = "# План\r\n  * [X] Мария — договор\r\nПримечание\r\n"
        try withFile(original) { root, file in
            let receipt = try change(in: root)
            XCTAssertTrue(try String(contentsOf: file, encoding: .utf8).contains("* [ ]"))
            XCTAssertEqual(TasksService.undoSync(receipt), .changed)
            XCTAssertEqual(try Data(contentsOf: file), Data(original.utf8))
        }
    }

    func testToggleOnlyChangesMarkerAndUndoRestoresLiteralCheckboxText() throws {
        let original = "- [ ] Проверить шаблоны [ ] и [x]\n"
        try withFile(original) { root, file in
            let receipt = try change(in: root)
            XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), "- [x] Проверить шаблоны [ ] и [x]\n")
            XCTAssertEqual(TasksService.undoSync(receipt), .changed)
            XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), original)
        }
    }

    /// Строка ушла с места — отмена отказывает: по тексту не отличить свою
    /// отметку от отметки владельца (№695, C3). Файл цел, отмена не сгорает.
    func testUndoRefusesMovedTaskAndKeepsTheFile() throws {
        try withFile("# План\n- [ ] Мария — договор\nЗаметка\n") { root, file in
            let receipt = try change(in: root)
            let edited = "Новая строка\n# План\n- [x] Мария — договор\nОбновлённая заметка\n"
            try edited.write(to: file, atomically: true, encoding: .utf8)
            XCTAssertEqual(TasksService.undoSync(receipt), .conflict)
            XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), edited)
        }
    }

    func testUndoDoesNotOverwriteExternallyEditedTask() throws {
        try withFile("- [ ] Мария — договор\n") { root, file in
            let receipt = try change(in: root)
            let edited = "- [x] Мария — новый договор\n"
            try edited.write(to: file, atomically: true, encoding: .utf8)
            XCTAssertEqual(TasksService.undoSync(receipt), .missing)
            XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), edited)
        }
    }

    func testUndoRefusesDuplicatesEvenWhenOriginalIndexStillMatches() throws {
        try withFile("- [ ] Мария — договор\n") { root, file in
            let receipt = try change(in: root)
            let edited = "- [x] Мария — договор\n- [x] Мария — договор\n"
            try edited.write(to: file, atomically: true, encoding: .utf8)
            XCTAssertEqual(TasksService.undoSync(receipt), .conflict)
            XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), edited)
        }
    }

    func testUndoDoesNotRecreateDeletedFile() throws {
        try withFile("- [ ] Мария — договор\n") { root, file in
            let receipt = try change(in: root)
            try FileManager.default.removeItem(at: file)
            XCTAssertEqual(TasksService.undoSync(receipt), .missing)
            XCTAssertFalse(FileManager.default.fileExists(atPath: file.path))
        }
    }

    func testUndoCannotRunTwiceAndReapplyTheChange() throws {
        try withFile("- [ ] Мария — договор\n") { root, file in
            let receipt = try change(in: root)
            XCTAssertEqual(TasksService.undoSync(receipt), .changed)
            XCTAssertEqual(TasksService.undoSync(receipt), .missing)
            XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), "- [ ] Мария — договор\n")
        }
    }

    private func waitForWrite(_ service: TasksService) async throws {
        for _ in 0..<500 {
            if !service.isMutating { return }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("Background task mutation did not finish")
    }

    func testServicePublishesUndoAndSerializesRepeatedClicks() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("task-undo-service-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("Минутки.md")
        let original = "- [ ] Первый договор\n- [ ] Второй договор\n"
        try original.write(to: file, atomically: true, encoding: .utf8)
        let items = TasksService.scanSync(graph: root)
        let first = try XCTUnwrap(items.first { $0.text == "Первый договор" })
        let second = try XCTUnwrap(items.first { $0.text == "Второй договор" })
        let service = TasksService()
        service.toggle(first, root: root)
        XCTAssertTrue(service.isMutating)
        service.toggle(second, root: root) // different row, same file: must be blocked
        service.undoLastChange() // must not race the unfinished write
        try await waitForWrite(service)
        XCTAssertEqual(service.lastChange?.item, first)
        XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), "- [x] Первый договор\n- [ ] Второй договор\n")
        service.undoLastChange()
        try await waitForWrite(service)
        XCTAssertNil(service.lastChange)
        XCTAssertNil(service.mutationError)
        XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), original)
    }

    func testFailedToggleKeepsPreviousUndoAndConflictingUndoReportsError() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("task-undo-conflict-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("Минутки.md")
        try "- [ ] Договор\n".write(to: file, atomically: true, encoding: .utf8)
        let item = try XCTUnwrap(TasksService.scanSync(graph: root).first)
        let service = TasksService()
        service.toggle(item, root: root)
        try await waitForWrite(service)
        let previous = try XCTUnwrap(service.lastChange)
        service.toggle(item, root: root) // stale snapshot is rejected
        try await waitForWrite(service)
        XCTAssertEqual(service.lastChange, previous)
        XCTAssertNotNil(service.mutationError)
        let edited = "- [x] Новый договор\n"
        try edited.write(to: file, atomically: true, encoding: .utf8)
        service.undoLastChange()
        try await waitForWrite(service)
        XCTAssertNil(service.lastChange)
        XCTAssertNotNil(service.mutationError)
        XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), edited)
    }

    /// Опыт O3 (№695, постфактум-2 по #794): два одинаковых поручения, отметили
    /// первое; владелец вручную снял его и отметил второе. Отмена не вправе
    /// стереть его отметку — та совпадает по тексту, но стоит на чужом месте.
    func testUndoDoesNotClearTheOwnersCheckOnAnIdenticalTwin() throws {
        let original = "- [ ] Договор\nзаметка\nещё\n- [ ] Договор\n"
        try withFile(original) { root, file in
            let receipt = try change(in: root)
            let swapped = "- [ ] Договор\nзаметка\nещё\n- [x] Договор\n"
            try swapped.write(to: file, atomically: true, encoding: .utf8)
            XCTAssertEqual(TasksService.undoSync(receipt), .conflict)
            XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), swapped)
            let shifted = "Новая строка\n" + swapped
            try shifted.write(to: file, atomically: true, encoding: .utf8)
            XCTAssertEqual(TasksService.undoSync(receipt), .conflict)
            XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), shifted)
            // Своя отметка на своём месте рядом с близнецом — обычная отмена.
            try "- [x] Договор\nзаметка\nещё\n- [ ] Договор\n".write(to: file, atomically: true, encoding: .utf8)
            XCTAssertEqual(TasksService.undoSync(receipt), .changed)
            XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), original)
        }
    }

    /// Вход C3 (№695, круг n695-p2): приложение отметило первое из двух
    /// одинаковых поручений; владелец удалил его строку и отметил второе.
    /// Близнеца в исходном состоянии в файле уже нет, совпавшая строка — чужая
    /// отметка на чужом месте: отмена не вправе её стереть.
    func testUndoDoesNotClearTheOwnersCheckAfterDeletingTheTwin() throws {
        try withFile("- [ ] Договор\nзаметка\nещё\n- [ ] Договор\n") { root, file in
            let receipt = try change(in: root)
            let owners = "заметка\nещё\n- [x] Договор\n"
            try owners.write(to: file, atomically: true, encoding: .utf8)
            XCTAssertEqual(TasksService.undoSync(receipt), .conflict)
            XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), owners)
        }
    }

    /// Конфликт отмену не сжигает: после правки файла владельцем отмену можно
    /// повторить (№695, постфактум-2 по #794).
    func testConflictingUndoKeepsTheUndoAvailable() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("task-undo-keep-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appendingPathComponent("Минутки.md")
        try "- [ ] Договор\n".write(to: file, atomically: true, encoding: .utf8)
        let item = try XCTUnwrap(TasksService.scanSync(graph: root).first)
        let service = TasksService()
        service.toggle(item, root: root)
        try await waitForWrite(service)
        let receipt = try XCTUnwrap(service.lastChange)
        let duplicated = "- [x] Договор\n- [x] Договор\n"
        try duplicated.write(to: file, atomically: true, encoding: .utf8)
        service.undoLastChange()
        try await waitForWrite(service)
        XCTAssertEqual(service.lastChange, receipt)
        XCTAssertNotNil(service.mutationError)
        XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), duplicated)
        try "- [x] Договор\n".write(to: file, atomically: true, encoding: .utf8)
        service.undoLastChange()
        try await waitForWrite(service)
        XCTAssertNil(service.lastChange)
        XCTAssertEqual(try String(contentsOf: file, encoding: .utf8), "- [ ] Договор\n")
    }
}
