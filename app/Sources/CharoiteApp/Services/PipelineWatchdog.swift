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

    /// Mac slept between two watchdog ticks: wall clock advanced while the
    /// uptime clock (which, like the daemon's `time.monotonic`, stands still
    /// in sleep) did not. Every age anchor is wall-clock, so right after
    /// waking they all read as the sleep duration and a perfectly healthy
    /// daemon would be restarted — and after three naps `giveUp` would end
    /// the meeting (review 21.08, DeepSeek). One re-arm tick is the cure.
    static func sleptBetweenTicks(
        wallDelta: TimeInterval,
        uptimeDelta: TimeInterval
    ) -> Bool {
        wallDelta - uptimeDelta > 30
    }
}
