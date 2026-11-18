import Combine
import Foundation

#if os(macOS)

struct MeetingActionCommand: Equatable, Sendable {
    let python: AppSettings.PythonScope
    let arguments: [String]

    var root: URL { python.dataRoot }
    var codeRoot: URL { python.codeRoot }
    var executable: URL { python.executable }

    static func participantProtocol(
        root: URL,
        meetingID: String,
        graph: URL
    ) -> MeetingActionCommand {
        let python = AppSettings.PythonScope.resolve(root: root)
        return MeetingActionCommand(
            python: python,
            arguments: [
                python.codeRoot.appendingPathComponent("scripts/protocol.py").path,
                archiveTarget(meetingID),
                "--graph", MeetingForgetScope.normalizedDirectory(graph).path,
                "--style", "plain",
            ])
    }

    static func forgetDryRun(
        scope: MeetingForgetScope,
        meetingID: String
    ) -> MeetingActionCommand {
        forget(scope: scope, meetingID: meetingID, apply: false)
    }

    fileprivate static func forgetApply(_ planned: PlannedForget) -> MeetingActionCommand {
        forget(scope: planned.scope, meetingID: planned.meeting.meetingID, apply: true)
    }

    private static func forget(
        scope: MeetingForgetScope,
        meetingID: String,
        apply: Bool
    ) -> MeetingActionCommand {
        var args = [
            scope.codeRoot.appendingPathComponent("scripts/forget_meeting.py").path,
            forgetTarget(meetingID),
            "--graph", scope.graph.path,
        ]
        // Копия аудио импортированной встречи живёт в папке импорта — путь
        // знает только приложение, скрипт без него её не найдёт (аудит 05.09)
        if let importFolder = scope.importFolder {
            args += ["--import-folder", importFolder]
        }
        if apply { args.append("--yes") }
        return MeetingActionCommand(python: scope.python, arguments: args)
    }

    /// Цель «забыть»: посекундный штамп, если ID его несёт («2026-08-03_113012»,
    /// с суффиксом коллизии «-1» включительно), иначе минута. Срез до минуты
    /// у посекундного ID заставлял скрипт угадывать владельца минуты по
    /// каталогу и делал соседку той же минуты (крэш-рестарт) незабываемой из
    /// приложения — «забыть» её стирало владельца (критика DS r3 по #499).
    static func forgetTarget(_ meetingID: String) -> String {
        let seconds = #"^\d{4}-\d{2}-\d{2}_\d{6}(?:-\d+)?"#
        if let range = meetingID.range(of: seconds, options: .regularExpression) {
            return String(meetingID[range])
        }
        return String(meetingID.prefix(15))
    }

    /// ID статуса содержит `_HHMM`, папка архива — ` HH-MM`.
    static func archiveTarget(_ meetingID: String) -> String {
        guard meetingID.count >= 15 else { return meetingID }
        let day = String(meetingID.prefix(10))
        let hourStart = meetingID.index(meetingID.startIndex, offsetBy: 11)
        let hourEnd = meetingID.index(hourStart, offsetBy: 2)
        let minuteEnd = meetingID.index(hourEnd, offsetBy: 2)
        return "\(day) \(meetingID[hourStart..<hourEnd])-\(meetingID[hourEnd..<minuteEnd])"
    }
}

/// Нормализованный снимок папок и интерпретатора, при которых строился сухой план удаления.
struct MeetingForgetScope: Equatable, Sendable {
    let python: AppSettings.PythonScope
    let graph: URL
    let importFolder: String?

    var root: URL { python.dataRoot }
    var codeRoot: URL { python.codeRoot }
    var executable: URL { python.executable }

    init(
        python: AppSettings.PythonScope,
        graph: URL,
        importFolder: String? = nil
    ) {
        self.python = python
        self.graph = Self.normalizedDirectory(graph)
        self.importFolder = Self.normalizedImportFolder(importFolder)
    }

    init(
        root: URL,
        graph: URL,
        importFolder: String? = nil
    ) {
        self.init(
            python: AppSettings.PythonScope.resolve(root: root),
            graph: graph,
            importFolder: importFolder)
    }

    static func normalizedDirectory(_ url: URL) -> URL {
        URL(fileURLWithPath: url.standardizedFileURL.path, isDirectory: true)
    }

    static func normalizedImportFolder(_ raw: String?) -> String? {
        guard let trimmed = raw?.trimmingCharacters(in: .whitespacesAndNewlines),
              !trimmed.isEmpty else { return nil }
        let expanded = (trimmed as NSString).expandingTildeInPath
        return URL(fileURLWithPath: expanded).standardizedFileURL.path
    }

    @MainActor
    static func current() -> MeetingForgetScope? {
        let python = AppSettings.PythonScope.resolve()
        guard let graph = AppSettings.graphDir(root: python.dataRoot) else { return nil }
        return MeetingForgetScope(
            python: python,
            graph: graph,
            importFolder: ImportService.configuredDir)
    }
}

/// Неделимый допуск на удаление: встреча, область папок и показанный человеку сухой план.
struct PlannedForget: Equatable, Sendable {
    let meeting: MeetingProcessingSnapshot
    let scope: MeetingForgetScope
    let planText: String
}

enum ForgetPlanOutcome: Equatable, Sendable {
    case ready(PlannedForget)
    case failed(String)
}

enum ForgetConfirmationDecision: Equatable, Sendable {
    case proceed(PlannedForget)
    case scopeChanged(String)
    case ignore
}

struct MeetingActionResult: Equatable, Sendable {
    let succeeded: Bool
    let text: String
}

/// Пользовательские действия над встречей используют уже проверенные CLI,
/// но больше не требуют терминала. Скрипты остаются единственным местом
/// правил безопасности: протокол не читает стенограмму, удаление сначала
/// строит план и требует отдельного подтверждения по тому же ID и путям.
enum MeetingActionsService {
    private static var graphNotConfiguredText: String {
        L.t("Папка графа не настроена.",
            "The graph folder is not configured.",
            "尚未配置图谱文件夹。")
    }

    @MainActor
    static func participantProtocol(_ snapshot: MeetingProcessingSnapshot) async -> MeetingActionResult {
        let root = AppSettings.charoiteRoot.standardizedFileURL
        guard let graph = AppSettings.graphDir(root: root) else {
            return MeetingActionResult(succeeded: false, text: graphNotConfiguredText)
        }
        return await execute(.participantProtocol(
            root: root,
            meetingID: snapshot.meetingID,
            graph: graph))
    }

    @MainActor
    static func forgetPlan(_ snapshot: MeetingProcessingSnapshot) async -> ForgetPlanOutcome {
        await forgetPlan(snapshot, scope: MeetingForgetScope.current())
    }

    static func forgetPlan(
        _ snapshot: MeetingProcessingSnapshot,
        scope: MeetingForgetScope?,
        runner: @Sendable (MeetingActionCommand) async -> MeetingActionResult = execute
    ) async -> ForgetPlanOutcome {
        guard let scope else {
            return .failed(graphNotConfiguredText)
        }
        let result = await runner(.forgetDryRun(
            scope: scope,
            meetingID: snapshot.meetingID))
        guard result.succeeded else {
            return .failed(result.text)
        }
        return .ready(PlannedForget(meeting: snapshot, scope: scope, planText: result.text))
    }

    static func forget(
        _ planned: PlannedForget,
        runner: @Sendable (MeetingActionCommand) async -> MeetingActionResult = execute
    ) async -> MeetingActionResult {
        await runner(.forgetApply(planned))
    }

    static func confirmForget(
        planned: PlannedForget?,
        currentMeeting: MeetingProcessingSnapshot,
        displayedMeeting: MeetingProcessingSnapshot?,
        isPresented: Bool,
        currentScope: MeetingForgetScope?
    ) -> ForgetConfirmationDecision {
        guard isPresented,
              let planned,
              planned.meeting.meetingID == currentMeeting.meetingID,
              planned.meeting.meetingID == displayedMeeting?.meetingID else {
            return .ignore
        }
        guard currentScope == planned.scope else {
            return .scopeChanged(L.t(
                "Настройки папок изменились после построения плана — откройте «Забыть встречу…» заново.",
                "Folder settings changed after building the plan — open “Forget meeting…” again.",
                "生成删除计划后文件夹设置已更改——请重新打开「忘记会议…」。"))
        }
        return .proceed(planned)
    }

    @Sendable
    fileprivate static func execute(_ command: MeetingActionCommand) async -> MeetingActionResult {
        await Task.detached(priority: .userInitiated) { run(command) }.value
    }

    nonisolated private static func run(_ command: MeetingActionCommand) -> MeetingActionResult {
        guard FileManager.default.isExecutableFile(atPath: command.executable.path) else {
            return MeetingActionResult(
                succeeded: false,
                text: L.t("Python-окружение Charoite не найдено. Откройте Настройки → Проверка.",
                          "Charoite's Python environment was not found. Open Settings → Check.",
                          "未找到 Charoite 的 Python 环境。请打开设置 → 检查。"))
        }
        let process = Process()
        let output = Pipe()
        process.arguments = command.arguments
        AppSettings.preparePython(process, scope: command.python)
        process.standardOutput = output
        process.standardError = output
        do {
            try process.run()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            let text = String(data: data, encoding: .utf8) ?? ""
            return MeetingActionResult(
                succeeded: process.terminationStatus == 0,
                text: text.trimmingCharacters(in: .whitespacesAndNewlines))
        } catch {
            return MeetingActionResult(succeeded: false, text: error.localizedDescription)
        }
    }
}

/// Координатор асинхронных действий карточки встречи: отсекает устаревшие ответы
/// при смене выбора A → B и связывает подтверждение удаления с конкретным планом.
@MainActor
final class MeetingCardActionController: ObservableObject {
    @Published private(set) var actionBusy = false
    @Published var actionMessage = ""
    @Published private(set) var plannedForget: PlannedForget?
    @Published private(set) var showForget = false

    private(set) var displayedMeeting: MeetingProcessingSnapshot?
    private var actionID: UUID?

    private let scopeProvider: @MainActor () -> MeetingForgetScope?
    private let runner: @Sendable (MeetingActionCommand) async -> MeetingActionResult
    private let protocolRunner: (@MainActor (MeetingProcessingSnapshot) async -> MeetingActionResult)?

    convenience init() {
        self.init(
            scopeProvider: { MeetingForgetScope.current() },
            runner: { await MeetingActionsService.execute($0) },
            protocolRunner: nil)
    }

    init(
        scopeProvider: @escaping @MainActor () -> MeetingForgetScope?,
        runner: @escaping @Sendable (MeetingActionCommand) async -> MeetingActionResult,
        protocolRunner: (@MainActor (MeetingProcessingSnapshot) async -> MeetingActionResult)? = nil
    ) {
        self.scopeProvider = scopeProvider
        self.runner = runner
        self.protocolRunner = protocolRunner
    }

    func appear(with snapshot: MeetingProcessingSnapshot) {
        if let displayedMeeting, displayedMeeting.meetingID != snapshot.meetingID {
            resetTransientState()
        }
        displayedMeeting = snapshot
    }

    func meetingChanged(to snapshot: MeetingProcessingSnapshot) {
        guard displayedMeeting?.meetingID != snapshot.meetingID else {
            displayedMeeting = snapshot
            return
        }
        resetTransientState()
        displayedMeeting = snapshot
    }

    func disappear() {
        resetTransientState()
        displayedMeeting = nil
    }

    func dismissForgetSheet() {
        guard !actionBusy else { return }
        showForget = false
        plannedForget = nil
    }

    func isForgetSheetPresented(for current: MeetingProcessingSnapshot) -> Bool {
        showForget
            && plannedForget?.meeting.meetingID == current.meetingID
            && displayedMeeting?.meetingID == current.meetingID
    }

    func copyParticipantProtocol(
        for snapshot: MeetingProcessingSnapshot,
        onCopy: @escaping @MainActor (String) -> Void
    ) {
        guard !actionBusy, displayedMeeting?.meetingID == snapshot.meetingID else { return }
        let id = UUID()
        actionID = id
        actionBusy = true
        actionMessage = ""
        let runProtocol = protocolRunner
        Task {
            let result: MeetingActionResult
            if let runProtocol {
                result = await runProtocol(snapshot)
            } else {
                result = await MeetingActionsService.participantProtocol(snapshot)
            }
            guard accepts(id, for: snapshot.meetingID) else { return }
            actionID = nil
            actionBusy = false
            if result.succeeded {
                onCopy(result.text)
                actionMessage = L.t(
                    "Безопасный протокол скопирован — стенограммы в нём нет.",
                    "Safe protocol copied — it contains no transcript.",
                    "安全纪要已复制——其中不含逐字稿。")
            } else {
                actionMessage = result.text
            }
        }
    }

    func prepareForget(for snapshot: MeetingProcessingSnapshot) {
        guard !actionBusy, displayedMeeting?.meetingID == snapshot.meetingID else { return }
        let id = UUID()
        actionID = id
        actionBusy = true
        actionMessage = ""
        plannedForget = nil
        showForget = false
        let scope = scopeProvider()
        Task {
            let outcome = await MeetingActionsService.forgetPlan(
                snapshot,
                scope: scope,
                runner: runner)
            guard accepts(id, for: snapshot.meetingID) else { return }
            actionID = nil
            actionBusy = false
            switch outcome {
            case .ready(let planned):
                plannedForget = planned
                showForget = true
            case .failed(let message):
                actionMessage = message
            }
        }
    }

    func runForget(
        for current: MeetingProcessingSnapshot,
        onDeleted: @escaping @MainActor (MeetingProcessingSnapshot) -> Void,
        onCloseCard: @escaping @MainActor () -> Void
    ) {
        guard !actionBusy else { return }
        let decision = MeetingActionsService.confirmForget(
            planned: plannedForget,
            currentMeeting: current,
            displayedMeeting: displayedMeeting,
            isPresented: showForget,
            currentScope: scopeProvider())
        switch decision {
        case .ignore:
            return
        case .scopeChanged(let message):
            showForget = false
            plannedForget = nil
            actionMessage = message
        case .proceed(let planned):
            let id = UUID()
            actionID = id
            actionBusy = true
            Task {
                let result = await MeetingActionsService.forget(planned, runner: runner)
                if result.succeeded {
                    onDeleted(planned.meeting)
                }
                guard accepts(id, for: planned.meeting.meetingID) else { return }
                actionID = nil
                actionBusy = false
                showForget = false
                plannedForget = nil
                if result.succeeded {
                    onCloseCard()
                } else {
                    actionMessage = result.text
                }
            }
        }
    }

    private func accepts(_ id: UUID, for meetingID: String) -> Bool {
        actionID == id && displayedMeeting?.meetingID == meetingID
    }

    private func resetTransientState() {
        actionID = nil
        actionBusy = false
        actionMessage = ""
        plannedForget = nil
        showForget = false
    }
}

#endif
