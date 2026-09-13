import XCTest
@testable import CharoiteiOS

/// Аудит 13.09 (DS I1 / GLM I1): «Уехало на Mac» печаталось по факту локального
/// копирования — выгрузка iCloud ещё не начиналась. Теперь файл очереди помечен
/// «скопирован, ждёт выгрузки», второй проход его не копирует заново, а сверяет.
final class InboxPendingMarkTests: XCTestCase {
    private let fm = FileManager.default
    private var made: [URL] = []

    override func tearDown() {
        for u in made { try? fm.removeItem(at: u) }
        made = []
        super.tearDown()
    }

    func testPendingMarkRoundTripsAndDropsWhenTheCopyIsGone() throws {
        let queued = Inbox.outbox.appendingPathComponent("test_pending.caf")
        try Data("звук".utf8).write(to: queued)
        made.append(queued)
        let dest = fm.temporaryDirectory.appendingPathComponent("test_pending_copy.caf")
        try Data("звук".utf8).write(to: dest)
        made.append(dest)

        XCTAssertNil(Inbox.pendingDest(for: queued), "без метки файл ещё не копировался")
        Inbox.markPending(queued, dest: dest)
        made.append(Inbox.pendingMark(for: queued))
        XCTAssertEqual(Inbox.pendingDest(for: queued)?.path, dest.path)
        XCTAssertFalse(Inbox.queued.contains(Inbox.pendingMark(for: queued)), "метка — не запись")

        try fm.removeItem(at: dest)
        XCTAssertNil(Inbox.pendingDest(for: queued), "копия исчезла — файл поедет заново")
        XCTAssertFalse(fm.fileExists(atPath: Inbox.pendingMark(for: queued).path), "метка снята")
    }

    func testLocalFolderCountsAsUploaded() throws {
        let local = fm.temporaryDirectory.appendingPathComponent("test_local.caf")
        try Data("звук".utf8).write(to: local)
        made.append(local)
        // вне iCloud ключей выгрузки нет: .uploaded или .unknown — оба значат «принято, убирать из очереди»
        let state = Inbox.uploadState(of: local)
        XCTAssertTrue(state == .uploaded || state == .unknown, "\(state)")
    }
}
