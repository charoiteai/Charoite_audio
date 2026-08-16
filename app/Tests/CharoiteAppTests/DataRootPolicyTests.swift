import XCTest
@testable import CharoiteApp

/// Откуда берётся папка данных — тоже вопрос о правах, а не о путях.
///
/// #328 закрыл подхват записываемого КОДА, но корень ДАННЫХ по-прежнему
/// брался из первого попавшегося клона `~/Charoite_audio` с `src/daemon.py`.
/// А папка данных — это `config.yaml` с `sufler.post_meeting_hook`: команда,
/// которую демон запускает после каждой встречи через shell. Пустой файл
/// `src/daemon.py` плюс свой `config.yaml` в записываемой папке — и
/// подписанное приложение выполняло команду со своими правами на микрофон
/// и экран (второе мнение по #328, 16.08).
final class DataRootPolicyTests: XCTestCase {

    /// Установка «из коробки»: данные — в Application Support, клон в домашней
    /// папке сам не берётся, даже если он там есть.
    func testВложенныйКодДанныеВApplicationSupport() {
        XCTAssertEqual(AppSettings.dataSource(embedded: true, explicitRoot: false), .workspace)
    }

    /// Явный выбор человека уважается всегда.
    func testЯвныйВыборПапкиУважается() {
        XCTAssertEqual(AppSettings.dataSource(embedded: true, explicitRoot: true), .chosenByHuman)
        XCTAssertEqual(AppSettings.dataSource(embedded: false, explicitRoot: true), .chosenByHuman)
    }

    /// Запуск из клона (без бандла): данные рядом с кодом, как и раньше.
    func testБезБандлаДанныеВКлоне() {
        XCTAssertEqual(AppSettings.dataSource(embedded: false, explicitRoot: false), .legacyClone)
    }

    /// Клон выглядит рабочим, выбора ещё не было, код в бандле — предложить
    /// один раз. Во всех остальных сочетаниях спрашивать не о чем.
    func testПредложениеТолькоКогдаЕстьЧтоРешать() {
        XCTAssertTrue(AppSettings.legacyCloneAwaitsChoice(
            embedded: true, explicitRoot: false, cloneLooksUsed: true))
        XCTAssertFalse(AppSettings.legacyCloneAwaitsChoice(
            embedded: true, explicitRoot: true, cloneLooksUsed: true),
            "уже выбрано — не переспрашивать")
        XCTAssertFalse(AppSettings.legacyCloneAwaitsChoice(
            embedded: true, explicitRoot: false, cloneLooksUsed: false),
            "клона нет — нечего предлагать")
        XCTAssertFalse(AppSettings.legacyCloneAwaitsChoice(
            embedded: false, explicitRoot: false, cloneLooksUsed: true),
            "без бандла клон и так папка данных")
    }
}
