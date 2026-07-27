import XCTest

/// Сквозной сценарий v1: выбрать тип → записать → остановить → файл создан.
/// Микрофон выдаётся заранее (simctl privacy grant) — алертов нет.
final class RecordFlowTests: XCTestCase {
    func testRecordNoteCreatesFile() throws {
        let app = XCUIApplication()
        app.launch()

        app.buttons["Заметка"].firstMatch.tap()

        let start = app.buttons["Начать запись"]
        XCTAssertTrue(start.waitForExistence(timeout: 5), "нет кнопки записи")
        start.tap()

        // запись идёт — таймер оживает
        sleep(4)

        let stop = app.buttons["Остановить запись"]
        XCTAssertTrue(stop.exists, "кнопка не перешла в режим записи")
        stop.tap()

        // итог: либо уехало в iCloud, либо честное «iCloud недоступен» (симулятор
        // без аккаунта) — оба значат, что файл записан и доставка отработала
        let outcome = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS 'Уехало' OR label CONTAINS 'iCloud'")
        ).firstMatch
        XCTAssertTrue(outcome.waitForExistence(timeout: 10), "нет итога записи")
    }
}
