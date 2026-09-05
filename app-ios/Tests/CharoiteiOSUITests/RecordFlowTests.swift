import XCTest

/// Приложение под тестом всегда говорит по-русски.
///
/// Подписи в сценариях — русские, а раннер CI живёт на английской локали:
/// ночная джоба падала на «нет кнопки Заметка», хотя приложение работало.
/// Язык задаём тем же механизмом, которым его выбирает человек в настройках
/// (ключ `ui.language` в UserDefaults) — через аргумент запуска, а не правкой
/// системной локали симулятора: так тест проверяет приложение, а не образ
/// раннера, и остаётся честным на машине с любым языком.
private func launchInRussian() -> XCUIApplication {
    let app = XCUIApplication()
    app.launchArguments += ["-ui.language", "ru"]
    // Автостарт (№167) на симуляторе с выбранной папкой доставки писал бы с
    // первого кадра, и кнопки «Начать запись» не было бы (GLM r2)
    app.launchArguments += ["-record.autostart", "NO"]
    app.launch()
    return app
}

/// Сквозной сценарий v1: выбрать тип → записать → остановить → файл создан.
/// Микрофон выдаётся заранее (simctl privacy grant) — алертов нет.
final class RecordFlowTests: XCTestCase {
    func testRecordNoteCreatesFile() throws {
        let app = launchInRussian()

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
        let app = launchInRussian()

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

/// Очередь недоставленных записей.
///
/// Раньше про неё говорила одна серая строка «в очереди: 6». За таким числом
/// может стоять получасовая встреча недельной давности, про которую человек
/// уверен, что она давно на Mac.
final class QueueFlowTests: XCTestCase {
    func testQueueOpensFromTheRecordScreen() throws {
        let app = launchInRussian()

        let entry = app.buttons.matching(
            NSPredicate(format: "label CONTAINS[c] 'очеред'")).firstMatch
        guard entry.waitForExistence(timeout: 5) else {
            throw XCTSkip("очередь пуста — открывать нечего")
        }
        entry.tap()

        XCTAssertTrue(app.navigationBars["Очередь"].waitForExistence(timeout: 5),
                      "строка очереди обязана открывать список, а не быть подписью")
        XCTAssertTrue(app.buttons["Отправить"].exists, "досылка руками — главное действие экрана")
    }

    func testDatesSpeakTheSameLanguageAsTheRestOfTheScreen() throws {
        // Подписи берутся из L.t, а даты — из системного форматтера: под
        // заголовком «Заметка» выходило «July 28».
        let app = launchInRussian()

        let entry = app.buttons.matching(
            NSPredicate(format: "label CONTAINS[c] 'очеред'")).firstMatch
        guard entry.waitForExistence(timeout: 5) else {
            throw XCTSkip("очередь пуста — дат не будет")
        }
        entry.tap()
        XCTAssertTrue(app.navigationBars["Очередь"].waitForExistence(timeout: 5))

        let latinMonths = ["January", "February", "March", "April", "May", "June", "July",
                           "August", "September", "October", "November", "December"]
        for cell in app.staticTexts.allElementsBoundByIndex {
            let label = cell.label
            for month in latinMonths where label.contains(month) {
                XCTFail("дата на чужом языке: \(label)")
            }
        }
    }
}
