import XCTest
@testable import CharoiteiOS

/// Очередь недоставленных записей как список, а не число.
///
/// Раньше про неё говорила одна серая строка «в очереди: 6». За таким числом
/// может стоять что угодно: шесть свежих файлов, которые уедут через минуту,
/// или получасовая встреча недельной давности, про которую человек уверен, что
/// она давно на Mac. 03.08 запись потерялась именно так — молча.
final class QueueItemTests: XCTestCase {
    private func item(_ name: String, minutesAgo: Double = 0, bytes: Int = 1024) -> Inbox.Item {
        Inbox.Item(url: URL(fileURLWithPath: "/tmp/\(name)"),
                   recorded: Date().addingTimeInterval(-minutesAgo * 60),
                   bytes: bytes)
    }

    func testKindIsReadFromThePrefixTheMacUses() {
        // тот же префикс, по которому Mac выбирает конвейер
        XCTAssertEqual(item("iphone_2026-08-03_1332.caf").name, "Встреча")
        XCTAssertEqual(item("note_iphone_2026-08-03_1332.caf").name, "Заметка")
        XCTAssertEqual(item("diary_iphone_2026-08-03_1332.caf").name, "Дневник")
    }

    func testFreshRecordingIsNotAnAlarm() {
        // обычная доставка занимает секунды, но «минуту назад» — ещё не диагноз
        XCTAssertFalse(item("iphone.caf", minutesAgo: 30).isStuck())
    }

    func testWaitingLongerThanADayIsAnAlarm() {
        // сутки — не про терпение, а про диагноз: всё, что висит дольше, висит
        // уже не «сейчас уедет»
        XCTAssertTrue(item("iphone.caf", minutesAgo: 25 * 60).isStuck())
    }

    func testBoundaryIsExactlyADay() {
        XCTAssertFalse(item("iphone.caf", minutesAgo: 23 * 60 + 59).isStuck())
    }

    func testWaitingTimeIsNeverNegative() {
        // часы на телефоне переводят, файлы приезжают из будущего — минус в
        // «ждёт N часов» выглядит как поломка приложения
        let future = Inbox.Item(url: URL(fileURLWithPath: "/tmp/a.caf"),
                                recorded: Date().addingTimeInterval(3600), bytes: 1)
        XCTAssertEqual(future.waiting(), 0)
        XCTAssertFalse(future.isStuck())
    }

    func testItemsAreIdentifiedByFileNotByName() {
        // в очереди легко лежат три «Заметки» — списку нужен разный id
        let a = item("note_1.caf")
        let b = item("note_2.caf")
        XCTAssertNotEqual(a.id, b.id)
        XCTAssertEqual(a.name, b.name)
    }
}
