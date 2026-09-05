import Foundation

#if os(macOS)

struct MeetingActionCommand: Equatable, Sendable {
    let executable: URL
    let arguments: [String]

    static func participantProtocol(
        root: URL,
        meetingID: String,
        graph: URL
    ) -> MeetingActionCommand {
        MeetingActionCommand(
            executable: AppSettings.pythonExecutable(root: root),
            arguments: [
                AppSettings.scriptPath("scripts/protocol.py", root: root),
                archiveTarget(meetingID),
                "--graph", graph.path,
                "--style", "plain",
            ])
    }

    static func forget(
        root: URL,
        meetingID: String,
        graph: URL,
        apply: Bool,
        importFolder: String? = nil
    ) -> MeetingActionCommand {
        var args = [
            AppSettings.scriptPath("scripts/forget_meeting.py", root: root),
            String(meetingID.prefix(15)),
            "--graph", graph.path,
        ]
        // Копия аудио импортированной встречи живёт в папке импорта — путь
        // знает только приложение, скрипт без него её не найдёт (аудит 05.09)
        if let importFolder, !importFolder.isEmpty {
            args += ["--import-folder", (importFolder as NSString).expandingTildeInPath]
        }
        if apply { args.append("--yes") }
        return MeetingActionCommand(
            executable: AppSettings.pythonExecutable(root: root),
            arguments: args)
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

struct MeetingActionResult: Equatable, Sendable {
    let succeeded: Bool
    let text: String
}

/// Пользовательские действия над встречей используют уже проверенные CLI,
/// но больше не требуют терминала. Скрипты остаются единственным местом
/// правил безопасности: протокол не читает стенограмму, удаление сначала
/// строит план и требует отдельного подтверждения.
enum MeetingActionsService {
    static func participantProtocol(_ snapshot: MeetingProcessingSnapshot) async -> MeetingActionResult {
        guard let graph = AppSettings.graphDir else {
            return MeetingActionResult(
                succeeded: false,
                text: L.t("Папка графа не настроена.",
                          "The graph folder is not configured.",
                          "尚未配置图谱文件夹。"))
        }
        return await execute(.participantProtocol(
            root: AppSettings.charoiteRoot,
            meetingID: snapshot.meetingID,
            graph: graph))
    }

    static func forgetPlan(_ snapshot: MeetingProcessingSnapshot) async -> MeetingActionResult {
        guard let graph = AppSettings.graphDir else {
            return MeetingActionResult(succeeded: false, text: L.t(
                "Папка графа не настроена.",
                "The graph folder is not configured.",
                "尚未配置图谱文件夹。"))
        }
        return await execute(.forget(
            root: AppSettings.charoiteRoot,
            meetingID: snapshot.meetingID,
            graph: graph,
            apply: false,
            importFolder: ImportService.configuredDir))
    }

    static func forget(_ snapshot: MeetingProcessingSnapshot) async -> MeetingActionResult {
        guard let graph = AppSettings.graphDir else {
            return MeetingActionResult(succeeded: false, text: L.t(
                "Папка графа не настроена.",
                "The graph folder is not configured.",
                "尚未配置图谱文件夹。"))
        }
        return await execute(.forget(
            root: AppSettings.charoiteRoot,
            meetingID: snapshot.meetingID,
            graph: graph,
            apply: true,
            importFolder: ImportService.configuredDir))
    }

    private static func execute(_ command: MeetingActionCommand) async -> MeetingActionResult {
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
        AppSettings.preparePython(process, executable: command.executable)
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

#endif
