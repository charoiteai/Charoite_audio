import XCTest
@testable import CharoiteApp

#if os(macOS)
@MainActor
final class MeetingActionsTests: XCTestCase {
    private func snapshot(
        id: String,
        title: String = "Встреча",
        review: MeetingProcessingSnapshot.ReviewStage? = nil
    ) -> MeetingProcessingSnapshot {
        MeetingProcessingSnapshot(
            schemaVersion: 1,
            meetingID: id,
            state: .ready,
            stage: "done",
            startedAt: 1_700_000_000,
            updatedAt: 1_700_000_100,
            transcriptPath: "/tmp/\(id).md",
            notePath: "/tmp/graph/Встречи/\(id).md",
            error: nil,
            review: review)
    }

    private func makeScope(
        root: String = "/tmp/charoite-root",
        codeRoot: String = "/tmp/charoite-code",
        executable: String = "/tmp/charoite-code/.venv/bin/python",
        graph: String = "/tmp/charoite-graph",
        importFolder: String? = "/tmp/charoite-inbox"
    ) -> MeetingForgetScope {
        MeetingForgetScope(
            root: URL(fileURLWithPath: root),
            codeRoot: URL(fileURLWithPath: codeRoot),
            executable: URL(fileURLWithPath: executable),
            graph: URL(fileURLWithPath: graph),
            importFolder: importFolder)
    }

    private func testPythonWithYAML() -> URL? {
        if let configured = ConfigEditCheck.testPython {
            return configured
        }
        let proc = Process()
        let out = Pipe()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        proc.arguments = ["-C", ConfigEditCheck.repo.path, "rev-parse", "--git-common-dir"]
        proc.standardOutput = out
        proc.standardError = FileHandle.nullDevice
        guard (try? proc.run()) != nil else { return nil }
        proc.waitUntilExit()
        guard proc.terminationStatus == 0,
              let raw = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines),
              !raw.isEmpty else { return nil }
        let gitCommon = URL(fileURLWithPath: raw, relativeTo: ConfigEditCheck.repo).standardizedFileURL
        let candidate = gitCommon.deletingLastPathComponent().appendingPathComponent(".venv/bin/python")
        return FileManager.default.isExecutableFile(atPath: candidate.path) ? candidate : nil
    }

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

    func testPrepareForgetDropsDelayedPlanAfterSwitchingToAnotherMeeting() async {
        let meetingA = snapshot(id: "2030-01-15_100000_A", title: "Встреча A")
        let meetingB = snapshot(id: "2030-01-15_110000_B", title: "Встреча B")
        let scope = makeScope()
        var recordedCommands: [MeetingActionCommand] = []
        var planStartedContinuation: CheckedContinuation<Void, Never>?
        var releasePlanContinuation: CheckedContinuation<MeetingActionResult, Never>?

        let controller = MeetingCardActionController(
            scopeProvider: { scope },
            runner: { command in
                await MainActor.run { recordedCommands.append(command) }
                if !command.arguments.contains("--yes") {
                    return await withCheckedContinuation { continuation in
                        Task { @MainActor in
                            releasePlanContinuation = continuation
                            planStartedContinuation?.resume()
                            planStartedContinuation = nil
                        }
                    }
                }
                return MeetingActionResult(succeeded: true, text: "забыто")
            })

        controller.appear(with: meetingA)
        await withCheckedContinuation { started in
            planStartedContinuation = started
            controller.prepareForget(for: meetingA)
        }

        controller.meetingChanged(to: meetingB)
        XCTAssertFalse(controller.actionBusy)

        releasePlanContinuation?.resume(returning: MeetingActionResult(succeeded: true, text: "план для A"))
        await Task.yield()
        try? await Task.sleep(nanoseconds: 20_000_000)

        XCTAssertFalse(controller.showForget, "просроченный план A не должен открываться поверх B")
        XCTAssertNil(controller.plannedForget)
        XCTAssertFalse(controller.isForgetSheetPresented(for: meetingB))

        var deleted = false
        controller.runForget(for: meetingB, onDeleted: { _ in deleted = true }, onCloseCard: {})
        await Task.yield()
        XCTAssertFalse(deleted)
        XCTAssertFalse(recordedCommands.contains { $0.arguments.contains("--yes") },
                       "подтверждение поверх B не должно запускать --yes")
    }

    func testPrepareForgetDropsStalePlanAfterRoundTripBackToSameMeeting() async {
        let meetingA = snapshot(id: "2030-01-15_100000_A", title: "Встреча A")
        let meetingB = snapshot(id: "2030-01-15_110000_B", title: "Встреча B")
        let scope = makeScope()
        var callCount = 0
        var firstPlanStarted: CheckedContinuation<Void, Never>?
        var releaseFirstPlan: CheckedContinuation<MeetingActionResult, Never>?

        let controller = MeetingCardActionController(
            scopeProvider: { scope },
            runner: { _ in
                let index = await MainActor.run { () -> Int in
                    callCount += 1
                    return callCount
                }
                if index == 1 {
                    return await withCheckedContinuation { continuation in
                        Task { @MainActor in
                            releaseFirstPlan = continuation
                            firstPlanStarted?.resume()
                            firstPlanStarted = nil
                        }
                    }
                }
                return MeetingActionResult(succeeded: true, text: "свежий план A")
            })

        controller.appear(with: meetingA)
        await withCheckedContinuation { started in
            firstPlanStarted = started
            controller.prepareForget(for: meetingA)
        }

        controller.meetingChanged(to: meetingB)
        controller.meetingChanged(to: meetingA)

        releaseFirstPlan?.resume(returning: MeetingActionResult(succeeded: true, text: "устаревший план A"))
        await Task.yield()
        try? await Task.sleep(nanoseconds: 20_000_000)

        XCTAssertFalse(controller.showForget, "ответ от прошлого показа A не должен открываться после возврата")
        XCTAssertNil(controller.plannedForget)

        controller.prepareForget(for: meetingA)
        for _ in 0..<20 where !controller.showForget {
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTAssertTrue(controller.showForget)
        XCTAssertEqual(controller.plannedForget?.planText, "свежий план A")
    }

    func testOpenForgetSheetSurvivesBackgroundUpdateOfSameMeetingAndClosesOnDifferentMeeting() async {
        let meetingA = snapshot(id: "2030-01-15_100000_A", title: "Встреча A")
        let meetingAUpdated = snapshot(
            id: "2030-01-15_100000_A",
            title: "Встреча A",
            review: MeetingProcessingSnapshot.ReviewStage(
                state: "done",
                result: .init(touched: 1, applied: 1)))
        let meetingB = snapshot(id: "2030-01-15_110000_B", title: "Встреча B")
        let scope = makeScope()
        var commands: [MeetingActionCommand] = []

        let controller = MeetingCardActionController(
            scopeProvider: { scope },
            runner: { command in
                await MainActor.run { commands.append(command) }
                return MeetingActionResult(succeeded: true, text: "ok")
            })

        controller.appear(with: meetingA)
        controller.prepareForget(for: meetingA)
        for _ in 0..<20 where !controller.showForget {
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTAssertTrue(controller.isForgetSheetPresented(for: meetingA))

        controller.meetingChanged(to: meetingAUpdated)
        XCTAssertTrue(controller.isForgetSheetPresented(for: meetingAUpdated),
                      "фоновое обновление review той же встречи не должно срывать диалог удаления")

        controller.meetingChanged(to: meetingB)
        XCTAssertFalse(controller.showForget)
        XCTAssertNil(controller.plannedForget)
        XCTAssertFalse(controller.isForgetSheetPresented(for: meetingB))

        // Повторно открываем для AUpdated и доводим удаление до конца.
        controller.meetingChanged(to: meetingAUpdated)
        controller.prepareForget(for: meetingAUpdated)
        for _ in 0..<20 where !controller.showForget {
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        var deletedIDs: [String] = []
        var closed = false
        controller.runForget(
            for: meetingAUpdated,
            onDeleted: { deletedIDs.append($0.meetingID) },
            onCloseCard: { closed = true })
        for _ in 0..<20 where deletedIDs.isEmpty {
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTAssertEqual(deletedIDs, [meetingA.meetingID])
        XCTAssertTrue(closed)
        XCTAssertTrue(commands.contains { $0.arguments.contains("--yes") && $0.arguments.contains("2030-01-15_100000") })
    }

    func testConfirmForgetRejectsScopeChangesAndExecutesPinnedScopeWhenUnchanged() async {
        let meetingA = snapshot(id: "2030-01-15_100000_A", title: "Встреча A")
        let initialScope = makeScope()
        let changedScopes: [MeetingForgetScope?] = [
            nil,
            makeScope(root: "/tmp/other-root"),
            makeScope(codeRoot: "/tmp/other-code"),
            makeScope(executable: "/tmp/other-python"),
            makeScope(graph: "/tmp/other-graph"),
            makeScope(importFolder: "/tmp/other-inbox"),
        ]

        for mutatedScope in changedScopes {
            var currentScope: MeetingForgetScope? = initialScope
            var executedApply = false
            let controller = MeetingCardActionController(
                scopeProvider: { currentScope },
                runner: { command in
                    if command.arguments.contains("--yes") {
                        await MainActor.run { executedApply = true }
                    }
                    return MeetingActionResult(succeeded: true, text: "план")
                })
            controller.appear(with: meetingA)
            controller.prepareForget(for: meetingA)
            for _ in 0..<20 where !controller.showForget {
                try? await Task.sleep(nanoseconds: 10_000_000)
            }
            currentScope = mutatedScope
            controller.runForget(for: meetingA, onDeleted: { _ in }, onCloseCard: {})
            await Task.yield()
            XCTAssertFalse(executedApply, "смена настроек до --yes обязана блокировать удаление")
            XCTAssertFalse(controller.showForget)
            XCTAssertFalse(controller.actionMessage.isEmpty)
        }

        // Тот же путь с хвостовым слэшем / ненормализованным .. считается неизменным.
        let equivalentScope = makeScope(
            root: "/tmp/charoite-root/",
            codeRoot: "/tmp/charoite-code/./",
            executable: "/tmp/charoite-code/.venv/bin/python",
            graph: "/tmp/charoite-graph/",
            importFolder: "/tmp/charoite-inbox/")
        XCTAssertEqual(initialScope, equivalentScope)

        var capturedCommand: MeetingActionCommand?
        let pinnedController = MeetingCardActionController(
            scopeProvider: { initialScope },
            runner: { command in
                if command.arguments.contains("--yes") {
                    await MainActor.run { capturedCommand = command }
                }
                return MeetingActionResult(succeeded: true, text: "план")
            })
        pinnedController.appear(with: meetingA)
        pinnedController.prepareForget(for: meetingA)
        for _ in 0..<20 where !pinnedController.showForget {
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        pinnedController.runForget(for: meetingA, onDeleted: { _ in }, onCloseCard: {})
        for _ in 0..<20 where capturedCommand == nil {
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTAssertEqual(capturedCommand?.root, initialScope.root)
        XCTAssertEqual(capturedCommand?.codeRoot, initialScope.codeRoot)
        XCTAssertEqual(capturedCommand?.executable, initialScope.executable)

        let process = Process()
        AppSettings.preparePython(
            process,
            root: initialScope.root,
            codeRootOverride: initialScope.codeRoot,
            executable: initialScope.executable)
        XCTAssertEqual(process.environment?["CHAROITE_ROOT"], initialScope.root.path)
        XCTAssertEqual(process.currentDirectoryURL?.standardizedFileURL, initialScope.codeRoot)
    }

    func testConfirmedForgetStillNotifiesRepositoryWhenCardClosesMidFlight() async {
        let meetingA = snapshot(id: "2030-01-15_100000_A", title: "Встреча A")
        let meetingB = snapshot(id: "2030-01-15_110000_B", title: "Встреча B")
        let scope = makeScope()
        var applyStarted: CheckedContinuation<Void, Never>?
        var releaseApply: CheckedContinuation<MeetingActionResult, Never>?

        let controller = MeetingCardActionController(
            scopeProvider: { scope },
            runner: { command in
                if command.arguments.contains("--yes") {
                    return await withCheckedContinuation { continuation in
                        Task { @MainActor in
                            releaseApply = continuation
                            applyStarted?.resume()
                            applyStarted = nil
                        }
                    }
                }
                return MeetingActionResult(succeeded: true, text: "план A")
            })

        controller.appear(with: meetingA)
        controller.prepareForget(for: meetingA)
        for _ in 0..<20 where !controller.showForget {
            try? await Task.sleep(nanoseconds: 10_000_000)
        }

        var deleted: [String] = []
        var closeCount = 0
        await withCheckedContinuation { started in
            applyStarted = started
            controller.runForget(
                for: meetingA,
                onDeleted: { deleted.append($0.meetingID) },
                onCloseCard: { closeCount += 1 })
        }

        // Пользователь переключился на B во время выполнения --yes.
        controller.meetingChanged(to: meetingB)
        controller.actionMessage = "сообщение для B"
        releaseApply?.resume(returning: MeetingActionResult(succeeded: true, text: "удалено"))
        for _ in 0..<20 where deleted.isEmpty {
            try? await Task.sleep(nanoseconds: 10_000_000)
        }

        XCTAssertEqual(deleted, [meetingA.meetingID], "успешное удаление обязано обновить общий список встреч")
        XCTAssertEqual(closeCount, 0, "завершение старого удаления не должно закрывать карточку B")
        XCTAssertEqual(controller.actionMessage, "сообщение для B")
        XCTAssertFalse(controller.actionBusy)
    }

    func testCopyProtocolDropsDelayedResultAfterMeetingSwitch() async {
        let meetingA = snapshot(id: "2030-01-15_100000_A", title: "Встреча A")
        let meetingB = snapshot(id: "2030-01-15_110000_B", title: "Встреча B")
        var copyStarted: CheckedContinuation<Void, Never>?
        var releaseCopy: CheckedContinuation<MeetingActionResult, Never>?

        let controller = MeetingCardActionController(
            scopeProvider: { nil },
            runner: { _ in MeetingActionResult(succeeded: false, text: "") },
            protocolRunner: { _ in
                await withCheckedContinuation { continuation in
                    Task { @MainActor in
                        releaseCopy = continuation
                        copyStarted?.resume()
                        copyStarted = nil
                    }
                }
            })

        controller.appear(with: meetingA)
        var copiedTexts: [String] = []
        await withCheckedContinuation { started in
            copyStarted = started
            controller.copyParticipantProtocol(for: meetingA) { copiedTexts.append($0) }
        }

        controller.meetingChanged(to: meetingB)
        releaseCopy?.resume(returning: MeetingActionResult(succeeded: true, text: "Протокол A"))
        await Task.yield()
        try? await Task.sleep(nanoseconds: 20_000_000)

        XCTAssertTrue(copiedTexts.isEmpty)
        XCTAssertEqual(controller.actionMessage, "")
    }

    func testSyntheticDryRunBuildsPlanForTargetMeetingWithoutDeletingFiles() async throws {
        let python = try XCTUnwrap(testPythonWithYAML(), "нужен python с PyYAML для синтетического dry-run")
        let fm = FileManager.default
        let tempDir = fm.temporaryDirectory
            .appendingPathComponent("forget-plan-\(UUID().uuidString)", isDirectory: true)
        defer { try? fm.removeItem(at: tempDir) }

        let dataRoot = tempDir.appendingPathComponent("data", isDirectory: true)
        let graphDir = tempDir.appendingPathComponent("graph", isDirectory: true)
        let transcriptsDir = dataRoot.appendingPathComponent("transcripts", isDirectory: true)
        let meetingsDir = graphDir.appendingPathComponent("Встречи", isDirectory: true)
        let configDir = dataRoot.appendingPathComponent("config", isDirectory: true)
        for dir in [transcriptsDir, meetingsDir, configDir] {
            try fm.createDirectory(at: dir, withIntermediateDirectories: true)
        }
        try "sufler:\n  user_name: owner\n  graph_dir: \(graphDir.path)\n"
            .write(to: configDir.appendingPathComponent("config.yaml"), atomically: true, encoding: .utf8)

        let transcriptA = transcriptsDir.appendingPathComponent("2030-01-15_100000_Первая.md")
        let transcriptB = transcriptsDir.appendingPathComponent("2030-01-15_110000_Вторая.md")
        let noteA = meetingsDir.appendingPathComponent("2030-01-15_1000.md")
        let noteB = meetingsDir.appendingPathComponent("2030-01-15_1100.md")
        for (file, body) in [(transcriptA, "стенограмма A"), (transcriptB, "стенограмма B"),
                             (noteA, "# Первая"), (noteB, "# Вторая")] {
            try body.write(to: file, atomically: true, encoding: .utf8)
        }

        let scope = MeetingForgetScope(
            root: dataRoot,
            codeRoot: ConfigEditCheck.repo,
            executable: python,
            graph: graphDir)
        let target = snapshot(id: "2030-01-15_100000_Первая", title: "Первая")

        let outcome = await MeetingActionsService.forgetPlan(target, scope: scope)
        guard case .ready(let planned) = outcome else {
            return XCTFail("ожидали сухой план, получили \(outcome)")
        }
        XCTAssertEqual(planned.meeting, target)
        XCTAssertEqual(planned.scope, scope)
        XCTAssertTrue(planned.planText.contains("2030-01-15_100000"), planned.planText)
        XCTAssertFalse(planned.planText.contains("2030-01-15_110000"), planned.planText)
        for file in [transcriptA, transcriptB, noteA, noteB] {
            XCTAssertTrue(fm.fileExists(atPath: file.path), "dry-run не смеет удалять \(file.lastPathComponent)")
        }
    }
}
#endif
