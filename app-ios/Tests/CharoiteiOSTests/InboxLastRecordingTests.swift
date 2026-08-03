import XCTest
@testable import CharoiteiOS

/// Кнопка «Поделиться записью» показывалась ровно тогда, когда доставка
/// сломалась: она смотрела в очередь недоставленного, а успешно уехавший файл
/// из очереди удалялся. То есть при штатной работе кнопки не было никогда —
/// именно в этом виде её и не нашли на телефоне 03.08.
///
/// Здесь проверяется, что «последняя запись» переживает доставку.
final class InboxLastRecordingTests: XCTestCase {
    private let fm = FileManager.default
    private var made: [URL] = []

    override func tearDown() {
        for u in made { try? fm.removeItem(at: u) }
        made = []
        super.tearDown()
    }

    /// Кладёт файл с заданной давностью. Отрицательная давность — «свежее
    /// всего, что есть»: контейнер тестового хоста не стерилен, и утверждать
    /// что-то про пустые папки значило бы проверять чужой мусор, а не код.
    private func put(_ name: String, in dir: URL, minutesAgo: Double) throws -> URL {
        let u = dir.appendingPathComponent(name)
        try Data("звук".utf8).write(to: u)
        try fm.setAttributes([.modificationDate: Date().addingTimeInterval(-minutesAgo * 60)],
                             ofItemAtPath: u.path)
        made.append(u)
        return u
    }

    func testDeliveredRecordingStaysShareable() throws {
        // файл уехал на Mac — из очереди он ушёл, но поделиться им можно
        let delivered = try put("test_delivered.caf", in: Inbox.sent, minutesAgo: -1)
        XCTAssertFalse(Inbox.queued.contains(delivered), "доставленного в очереди нет")
        XCTAssertEqual(Inbox.lastRecording?.lastPathComponent, "test_delivered.caf",
                       "поделиться можно и тем, что уже уехало")
    }

    func testFreshestWinsAcrossBothFolders() throws {
        _ = try put("test_old.caf", in: Inbox.sent, minutesAgo: 60)
        _ = try put("test_new.caf", in: Inbox.outbox, minutesAgo: -1)
        XCTAssertEqual(Inbox.lastRecording?.lastPathComponent, "test_new.caf",
                       "делимся последней записью, а не последней доставленной")
    }

    func testQueueStaysListOfDebtsOnly() throws {
        // отправленное не должно возвращаться в очередь: иначе оно поедет в
        // iCloud на каждом flush по кругу
        _ = try put("test_sent.caf", in: Inbox.sent, minutesAgo: 5)
        XCTAssertFalse(Inbox.queued.contains { $0.lastPathComponent == "test_sent.caf" })
    }

    func testKeepSentStaysModest() {
        // копия последних встреч — страховка, а не второй архив на телефоне
        XCTAssertGreaterThanOrEqual(Inbox.keepSent, 1)
        XCTAssertLessThanOrEqual(Inbox.keepSent, 10)
    }
}
