import XCTest
@testable import CharoiteApp

/// Координатор выхода (тест 9, №622): набор ожиданий → ровно один ответ.
final class TerminationCoordinatorTests: XCTestCase {

    func testДваОжиданияОдинОтвет() {
        var c = TerminationCoordinator()
        XCTAssertEqual(c.begin(recording: true, installing: true, askStay: { false }), .later([.sufler, .install]))
        XCTAssertTrue(c.inProgress)
        XCTAssertFalse(c.finish(.install))
        XCTAssertFalse(c.finish(.install), "повторное снятие")
        XCTAssertTrue(c.finish(.sufler))
        XCTAssertFalse(c.finish(.sufler), "второго ответа нет")
        XCTAssertFalse(c.finish(.install))
    }

    func testПовторныйВходБезДиалогаИБезВторогоОтвета() {
        var c = TerminationCoordinator()
        var asked = 0
        XCTAssertEqual(c.begin(recording: true, installing: false, askStay: { asked += 1; return false }), .later([.sufler]))
        XCTAssertEqual(c.begin(recording: true, installing: true, askStay: { asked += 1; return false }), .alreadyInProgress)
        XCTAssertEqual(asked, 1)
        XCTAssertFalse(c.finish(.install), "чужое ожидание не отвечает")
        XCTAssertTrue(c.finish(.sufler))
    }

    func testТолькоУстановкаБезДиалога() {
        var c = TerminationCoordinator()
        XCTAssertEqual(c.begin(recording: false, installing: true, askStay: { XCTFail("диалог"); return true }), .later([.install]))
        XCTAssertTrue(c.finish(.install))
    }

    func testНичегоНеИдётВыходСразу() {
        var c = TerminationCoordinator()
        XCTAssertEqual(c.begin(recording: false, installing: false, askStay: { XCTFail("диалог"); return true }), .now)
        XCTAssertFalse(c.inProgress)
    }

    /// Решение главной по D C2: после «остаться» флаг не стоит, второй Cmd-Q
    /// снова показывает диалог.
    func testОстатьсяПотомСноваДиалог() {
        var c = TerminationCoordinator()
        var asked = 0
        XCTAssertEqual(c.begin(recording: true, installing: true, askStay: { asked += 1; return true }), .cancel)
        XCTAssertFalse(c.inProgress)
        XCTAssertEqual(c.begin(recording: true, installing: true, askStay: { asked += 1; return false }),
                       .later([.sufler, .install]))
        XCTAssertEqual(asked, 2, "диалог показан снова")
    }

    func testВыйтиПотомВходБезДиалога() {
        var c = TerminationCoordinator()
        var asked = 0
        _ = c.begin(recording: true, installing: false, askStay: { asked += 1; return false })
        XCTAssertEqual(c.begin(recording: true, installing: false, askStay: { asked += 1; return false }), .alreadyInProgress)
        XCTAssertEqual(asked, 1)
    }
}
