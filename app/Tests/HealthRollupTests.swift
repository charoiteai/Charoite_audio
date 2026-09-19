import Foundation
import XCTest
@testable import CharoiteApp

/// №139: свёртка здоровья — таблица «состояние → ярус», один вход для иконки
/// и строки меню. Красный — только «данные гибнут» при записи; всё прочее —
/// жёлтое; ранг источников прибит (запись → обработка → Ollama → ночь); слова
/// — только владельцев; кто красит какую поверхность — `HealthPresentation`.
final class HealthRollupTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_758_000_000)

    private func verdict(recording: PipelineHealthProblem? = nil, isRecording: Bool = false,
                         processingError: String? = nil,
                         ollama: OllamaRuntime = .running,
                         nightly: NightlyState = .ok(finished: Date()),
                         agent: Bool = true) -> HealthVerdict {
        HealthRollup.rollup(recording: recording, isRecording: isRecording,
                            processingError: processingError, ollama: ollama,
                            nightly: nightly, nightlyAgentConfigured: agent)
    }

    func testAllClearWhenOwnersReportNothing() {
        XCTAssertEqual(verdict(), .allClear)
        XCTAssertNil(verdict().headline)
        XCTAssertEqual(verdict(ollama: .unknown), .allClear, "пробы ещё не было — не сигнал и не «работает»")
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
        let worst = verdict(processingError: "Не удалось обработать встречу — стенограмма сохранена",
                            ollama: .notInstalled(canUseBrew: false),
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
        for state in [NightlyState.ok(finished: now), .running(started: now)] {
            XCTAssertEqual(verdict(nightly: state), .allClear, "\(state): норма")
        }
        // .never: без агента — не настроено, не проблема иконки; с агентом — ночь не отработала ни разу
        XCTAssertEqual(verdict(nightly: .never, agent: false), .allClear)
        XCTAssertEqual(verdict(nightly: .never, agent: true).tier, .degraded)
        XCTAssertEqual(verdict(nightly: .never, agent: true).headline, NightlyStatusService.title(for: .never))
    }

    func testOllamaIsDegradedWheneverProbedAndNotRunningWithTheOwnersWords() {
        let down = verdict(ollama: .installedNotRunning(launcher: .brewService))
        XCTAssertEqual(down.tier, .degraded)
        XCTAssertEqual(down.headline, OllamaRuntimeService.explanation(for: .installedNotRunning(launcher: .brewService)))
        XCTAssertEqual(verdict(ollama: .notInstalled(canUseBrew: true)).tier, .degraded)
        XCTAssertEqual(OllamaRuntimeService.actionTitle(for: .unknown), "", "кнопки у «неизвестно» нет")
        XCTAssertFalse(OllamaRuntimeService.explanation(for: .unknown).isEmpty)
    }

    func testProcessingUsesTheOwnersHeadlineNotItsOwnWords() {
        let owner = "Статус обработки не появился — стенограмма сохранена, проверьте logs/"
        let v = verdict(processingError: owner)
        XCTAssertEqual(v.signals.map(\.source), [.processing])
        XCTAssertEqual(v.headline, owner)
        XCTAssertEqual(verdict(processingError: ""), .allClear, "пустой заголовок — ошибки нет")
    }

    func testSourcesAreOrderedByRankAndWorstTierWins() {
        let all = verdict(recording: .lagging(backlogSeconds: 30), isRecording: true, processingError: "ошибка",
                          ollama: .notInstalled(canUseBrew: false), nightly: .stale(finished: now))
        XCTAssertEqual(all.signals.map(\.source), [.recording, .processing, .ollama, .nightly])
        XCTAssertEqual(all.tier, .degraded)
        let critical = verdict(recording: .pumpDead, isRecording: true, processingError: "ошибка")
        XCTAssertEqual(critical.tier, .critical)
        XCTAssertEqual(critical.headline, PipelineHealthPresentation.text(for: .pumpDead))
        // ранг — свойство enum (Comparable), не порядок append
        XCTAssertLessThan(HealthSource.recording, HealthSource.nightly)
        XCTAssertEqual(HealthSource.allCases, HealthSource.allCases.sorted())
        XCTAssertEqual(HealthSource.allCases.count, 4, "новый источник — новая строка таблицы и тест")
    }

    func testPresentationChoosesSourcePerSurface() {
        // здоровая запись при проспанной ночи: иконка жёлтая, точка записи — зелёная
        let v = verdict(isRecording: true, nightly: .slept(finished: now, minutes: 300, steps: ["досье"]))
        XCTAssertEqual(HealthPresentation.iconTier(v), .degraded)
        XCTAssertEqual(HealthPresentation.recordingDotTier(v), .ok)
        XCTAssertEqual(HealthPresentation.iconLabel(v), NightlyStatusService.title(for: .slept(finished: now, minutes: 300, steps: ["досье"])))
        // деградация самой записи — точка жёлтая; отказ диска — красная
        XCTAssertEqual(HealthPresentation.recordingDotTier(verdict(recording: .lagging(backlogSeconds: 40), isRecording: true)), .degraded)
        XCTAssertEqual(HealthPresentation.recordingDotTier(verdict(recording: .recordingUnavailable(channels: []), isRecording: true)), .critical)
    }

    func testMenuLineOrderWorkAboveProblemAboveReadyAboveIdle() {
        let problem = verdict(nightly: .slept(finished: now, minutes: 300, steps: ["досье"]))
        // идёт работа — живая строка владельца, жалоба не вытесняет её (Critical DS круга 1)
        XCTAssertEqual(HealthPresentation.menuLine(problem, isRecording: false, activityText: "Распознаю речь…",
                                                   hasReadyMeeting: false), .activity("Распознаю речь…"))
        // владелец отдал nil (ошибка вместо работы) — слот активности не занят, показана проблема (Important DS круга 2)
        XCTAssertEqual(HealthPresentation.menuLine(problem, isRecording: false, activityText: nil, hasReadyMeeting: true),
                       .problem(problem.headline!, .degraded))
        XCTAssertEqual(HealthPresentation.menuLine(problem, isRecording: false, activityText: "", hasReadyMeeting: false),
                       .problem(problem.headline!, .degraded))
        // без проблем — готовность, потом покой
        XCTAssertEqual(HealthPresentation.menuLine(.allClear, isRecording: false, activityText: nil, hasReadyMeeting: true), .ready)
        XCTAssertEqual(HealthPresentation.menuLine(.allClear, isRecording: false, activityText: nil, hasReadyMeeting: false), .idle)
        XCTAssertEqual(MenuLine.ready.text, L.t("Встреча готова", "Meeting ready", "会议已就绪"))
        // запись — всегда «Запись ·», ярус только по записи
        let rec = HealthPresentation.menuLine(problem, isRecording: true, activityText: nil, hasReadyMeeting: false)
        XCTAssertEqual(rec, .recording(.ok))
        XCTAssertTrue(rec.text.hasSuffix("·"))
        XCTAssertEqual(HealthPresentation.menuLine(verdict(recording: .pumpDead, isRecording: true), isRecording: true,
                                                   activityText: nil, hasReadyMeeting: false), .recording(.critical))
    }

    /// Поведение, не подстрочник: проба «не состоялась» (nil) не трогает state, «порт
    /// молчит» (false) — пишет (Minor DS круга 3 по №139).
    @MainActor
    func testInconclusiveProbeKeepsTheOwnersState() async {
        let service = OllamaRuntimeService.shared
        let saved = OllamaRuntimeService.probe
        defer { OllamaRuntimeService.probe = saved }
        OllamaRuntimeService.probe = { true }
        await service.refresh()
        XCTAssertEqual(service.state, .running)
        OllamaRuntimeService.probe = { nil }
        await service.refresh()
        XCTAssertEqual(service.state, .running, "неизвестность не перезаписывает факт")
        OllamaRuntimeService.probe = { false }
        await service.refresh()
        XCTAssertNotEqual(service.state, .running, "отказ соединения — факт «не запущен / не установлен»")
        XCTAssertNotEqual(service.state, .unknown)
    }

    func testOnlyConnectionRefusalCountsAsSilence() {
        XCTAssertTrue(OllamaRuntimeService.refused(.cannotConnectToHost))
        XCTAssertTrue(OllamaRuntimeService.refused(.cannotFindHost))
        for code in [URLError.Code.timedOut, .cancelled, .networkConnectionLost, .notConnectedToInternet, .badServerResponse] {
            XCTAssertFalse(OllamaRuntimeService.refused(code), "\(code): неизвестность, не факт")
        }
    }

    func testTierOrderingIsTotal() {
        XCTAssertLessThan(HealthTier.ok, .degraded)
        XCTAssertLessThan(HealthTier.degraded, .critical)
        XCTAssertEqual([HealthTier.degraded, .ok, .critical].max(), .critical)
    }

    /// Структурно: слова о фактах владельцев не рождаются в свёртке, вью меню-бара
    /// не читает ярус мимо политики представления, свежесть — у одного планировщика
    /// (Как чинить DS и GLM круга 1).
    func testOwnershipGatesHold() throws {
        let app = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        let sources = app.appendingPathComponent("Sources/CharoiteApp")
        func body(of type: String, in text: String) throws -> String {
            // тело типа — от объявления до следующего объявления верхнего уровня
            let start = try XCTUnwrap(text.range(of: type), "\(type) не найден")
            let tail = text[start.upperBound...]
            let end = tail.range(of: "\n}\n")?.upperBound ?? tail.endIndex
            return String(tail[..<end])
        }
        let rollup = try String(contentsOf: sources.appendingPathComponent("Services/HealthRollup.swift"), encoding: .utf8)
        XCTAssertFalse(try body(of: "enum HealthRollup {", in: rollup).contains("L.t("),
                       "свёртка не сочиняет слов о фактах владельцев")
        XCTAssertFalse(try body(of: "enum HealthPresentation {", in: rollup).contains("L.t("),
                       "политика поверхностей не сочиняет слов о фактах владельцев")
        XCTAssertFalse(rollup.contains("static func stop()"), "мёртвого API у планировщика нет")

        let menu = try String(contentsOf: sources.appendingPathComponent("Views/MenuBar/MenuBarView.swift"), encoding: .utf8)
        for forbidden in ["verdict.tier", ".tier(of:", "statusText", "actionTitle != nil", "await HealthClock.tick"] {
            XCTAssertFalse(menu.contains(forbidden), "вью меню-бара читает только политику и владельцев: \(forbidden)")
        }
        XCTAssertTrue(menu.contains("HealthClock.requestTick()"), "открытие меню лишь просит тик вне своего жизненного цикла")
        // ни одна вью не освежает владельца здоровья сама — правило для всех поверхностей (Important DS круга 3)
        let views = try FileManager.default.subpathsOfDirectory(atPath: sources.appendingPathComponent("Views").path)
            .filter { $0.hasSuffix(".swift") }
        for rel in views {
            let text = try String(contentsOf: sources.appendingPathComponent("Views/\(rel)"), encoding: .utf8)
            XCTAssertFalse(text.contains("nightly.refresh()") || text.contains("ollama.refresh()")
                           || text.contains("NightlyStatusService.shared.refresh()"),
                           "\(rel): свежесть ночи и Ollama — у HealthClock, не у вью")
        }

        let nightly = try String(contentsOf: sources.appendingPathComponent("Services/NightlyStatusService.swift"), encoding: .utf8)
        XCTAssertFalse(nightly.contains("Timer.scheduledTimer"), "один планировщик на приложение, не таймер на владельца")
        XCTAssertNotNil(nightly.range(of: #"private init\(\)\s*\{\s*\}"#, options: .regularExpression),
                        "init без тела: чтение диска не на пути рендера иконки")
        let today = try String(contentsOf: sources.appendingPathComponent("Views/Workspace/TodayWorkspaceView.swift"), encoding: .utf8)
        XCTAssertTrue(today.contains("NightlyStatus.isProblem("), "«Сегодня» красит ночь той же политикой, что иконка")

        let ollama = try String(contentsOf: sources.appendingPathComponent("Services/OllamaRuntimeService.swift"), encoding: .utf8)
        XCTAssertTrue(ollama.contains("static func responds() async -> Bool?"), "у пробы три исхода: отмена — не «порт молчит»")
        XCTAssertFalse(ollama.contains("var needsAttention"), "мёртвых предикатов у владельцев нет")

        let appFile = try String(contentsOf: sources.appendingPathComponent("App/CharoiteApp.swift"), encoding: .utf8)
        XCTAssertTrue(appFile.contains("HealthClock.start()"))
    }
}
