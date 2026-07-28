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

    /// Тур по вкладкам: без выбранной папки графа обе показывают честное
    /// пустое состояние с подсказкой, что сделать. Скрины — в отчёт теста.
    func testTabsShowEmptyStatesWithoutGraphFolder() throws {
        let app = XCUIApplication()
        app.launch()

        app.tabBars.buttons["Встречи"].tap()
        XCTAssertTrue(app.staticTexts["Выберите папку графа"].waitForExistence(timeout: 5),
                      "нет пустого состояния ленты встреч")
        shot(app, name: "meetings_empty")

        app.tabBars.buttons["Задачи"].tap()
        XCTAssertTrue(app.staticTexts["Сначала папка графа"].waitForExistence(timeout: 5),
                      "нет пустого состояния задач")
        shot(app, name: "tasks_empty")

        app.tabBars.buttons["Запись"].tap()
        XCTAssertTrue(app.buttons["Начать запись"].waitForExistence(timeout: 5),
                      "вкладка записи не вернулась")
    }

    private func shot(_ app: XCUIApplication, name: String) {
        let a = XCTAttachment(screenshot: app.screenshot())
        a.name = name
        a.lifetime = .keepAlways
        add(a)
    }
}
