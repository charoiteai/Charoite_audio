import Combine
import XCTest
@testable import CharoiteApp

/// Общая занятость двух установок — по методам, а не по кнопкам (№622).
@MainActor
final class EngineInstallActivityTests: XCTestCase {
    private let key = ModelPullService.diarizationKey

    override func tearDown() async throws {
        ModelPullService.shared.progress[key] = nil
        NemotronEngineService.shared.action = .idle
    }

    func testНаборГолосовНеЗапускаетсяПриУстановкеNemotron() {
        let pulls = ModelPullService.shared
        NemotronEngineService.shared.action = .running(.install, lastLine: "")
        pulls.pullDiarization()
        XCTAssertNil(pulls.progress[key], "вторая загрузка в тот же models/diar/*.part")
        XCTAssertEqual(pulls.failed[key], EngineInstallActivity.busyText, "отказ не молчит")
    }

    func testПубликуетПриСменеЛюбойИзДвухСлужб() {
        var count = 0
        let sub = EngineInstallActivity.shared.objectWillChange.sink { count += 1 }
        defer { sub.cancel() }
        ModelPullService.shared.progress[key] = "качаю…"
        XCTAssertTrue(EngineInstallActivity.shared.busy)
        XCTAssertEqual(count, 1)
        ModelPullService.shared.progress[key] = nil
        XCTAssertFalse(EngineInstallActivity.shared.busy)
        XCTAssertEqual(count, 2)
        NemotronEngineService.shared.action = .running(.check, lastLine: "")
        XCTAssertTrue(EngineInstallActivity.shared.busy)
        XCTAssertEqual(count, 3)
        NemotronEngineService.shared.action = .cancelling(afterCommit: false)
        XCTAssertEqual(count, 3, "busy не менялся")
        NemotronEngineService.shared.action = .idle
        XCTAssertFalse(EngineInstallActivity.shared.busy)
        XCTAssertEqual(count, 4)
    }
}
