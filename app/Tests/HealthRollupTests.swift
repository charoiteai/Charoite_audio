import Foundation
import XCTest
@testable import CharoiteApp

/// №139: свёртка здоровья — таблица «состояние → ярус», один вход для иконки
/// и строки меню. Красный — только «данные гибнут» при записи; всё прочее —
/// жёлтое; ранг источников прибит (запись → обработка → Ollama → ночь).
final class HealthRollupTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_758_000_000)

    private func verdict(recording: PipelineHealthProblem? = nil, isRecording: Bool = false,
                         processingError: Bool = false,
                         ollama: OllamaRuntime = .running,
                         nightly: NightlyState = .ok(finished: Date())) -> HealthVerdict {
        HealthRollup.rollup(recording: recording, isRecording: isRecording,
                            processingError: processingError, ollama: ollama, nightly: nightly)
    }

    func testAllClearWhenOwnersReportNothing() {
        XCTAssertEqual(verdict(), .allClear)
        XCTAssertNil(verdict().headline)
    }

    func testRedIsReservedForDataLossDuringRecording() {
        XCTAssertEqual(verdict(recording: .recordingUnavailable(channels: ["mic"]), isRecording: true).tier, .critical)
        XCTAssertEqual(verdict(recording: .pumpDead, isRecording: true).tier, .critical)
        // зависший STT и отставание — аудио на диске есть
        XCTAssertEqual(verdict(recording: .stalled(stage: "transcription", seconds: 120), isRecording: true).tier, .degraded)
        XCTAssertEqual(verdict(recording: .lagging(backlogSeconds: 40), isRecording: true).tier, .degraded)
        // вне записи монитор хранит прошлое — не сигнал (гейт isRunning, как у pipelineStatusText)
        XCTAssertEqual(verdict(recording: .recordingUnavailable(channels: []), isRecording: false), .allClear)
        // ничто вне записи не даёт красного
        let worst = verdict(processingError: true, ollama: .notInstalled(canUseBrew: false),
                            nightly: .failed(finished: now, steps: ["ядра"]))
        XCTAssertEqual(worst.tier, .degraded)
    }

    func testNightlyTableOnlyProblemsReachTheIcon() {
        let problems: [NightlyState] = [
            .failed(finished: now, steps: ["ядра"]), .slept(finished: now, minutes: 300, steps: ["досье"]),
            .interrupted(started: now), .stale(finished: now), .foreignScript(path: "/x/nightly.sh"),
        ]
        for state in problems {
            XCTAssertEqual(verdict(nightly: state).tier, .degraded, "\(state)")
            XCTAssertEqual(verdict(nightly: state).headline, NightlyStatusService.title(for: state))
        }
        for state in [NightlyState.ok(finished: now), .running(started: now), .never] {
            XCTAssertEqual(verdict(nightly: state), .allClear, "\(state): норма или не настроено — не проблема иконки")
        }
    }

    func testOllamaIsDegradedWheneverNotRunningWithTheOwnersWords() {
        let down = verdict(ollama: .installedNotRunning(launcher: .brewService))
        XCTAssertEqual(down.tier, .degraded)
        XCTAssertEqual(down.headline, OllamaRuntimeService.explanation(for: .installedNotRunning(launcher: .brewService)))
        XCTAssertEqual(verdict(ollama: .notInstalled(canUseBrew: true)).tier, .degraded)
    }

    func testSourcesAreOrderedByRankAndWorstTierWins() {
        let all = verdict(recording: .lagging(backlogSeconds: 30), isRecording: true, processingError: true,
                          ollama: .notInstalled(canUseBrew: false), nightly: .stale(finished: now))
        XCTAssertEqual(all.signals.map(\.source), [.recording, .processing, .ollama, .nightly])
        XCTAssertEqual(all.tier, .degraded)
        let critical = verdict(recording: .pumpDead, isRecording: true, processingError: true)
        XCTAssertEqual(critical.tier, .critical)
        XCTAssertEqual(critical.headline, PipelineHealthPresentation.text(for: .pumpDead))
        // Ollama показывается раньше ночи; ни одна строка не пустая и все — от владельцев
        let two = verdict(ollama: .notInstalled(canUseBrew: false), nightly: .stale(finished: now))
        XCTAssertEqual(two.signals.map(\.source), [.ollama, .nightly])
        XCTAssertTrue(two.signals.allSatisfy { !$0.text.isEmpty })
        XCTAssertEqual(HealthSource.allCases.count, 4, "новый источник — новая строка таблицы и тест")
    }

    func testTierOrderingIsTotal() {
        XCTAssertLessThan(HealthTier.ok, .degraded)
        XCTAssertLessThan(HealthTier.degraded, .critical)
        XCTAssertEqual([HealthTier.degraded, .ok, .critical].max(), .critical)
    }
}
