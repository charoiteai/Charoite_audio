import Foundation

/// Pure decision for the runtime monitor; process control stays in
/// `SuflerService`, while every combination of signals is testable here.
enum PipelineWatchdog {
    /// Longer than the 5-second STT progress and 30-second daemon heartbeat,
    /// so one unusually heavy chunk cannot create a restart loop.
    static let timeout: TimeInterval = 100

    static func shouldRestart(
        daemonEventAge: TimeInterval,
        sttProgressAge: TimeInterval?,
        audioInputAge: TimeInterval?
    ) -> Bool {
        if daemonEventAge > timeout { return true }
        if let sttProgressAge, sttProgressAge > timeout { return true }
        if let audioInputAge, audioInputAge > timeout { return true }
        return false
    }
}
