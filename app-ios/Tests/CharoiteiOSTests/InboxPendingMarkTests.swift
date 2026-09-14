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
        // всё во временном каталоге: очередь тестового хоста — не место для мусора (DS M10)
        let queued = fm.temporaryDirectory.appendingPathComponent("test_pending.caf")
        try Data("звук".utf8).write(to: queued)
        made.append(queued)
        let dest = fm.temporaryDirectory.appendingPathComponent("test_pending_copy.caf")
        try Data("звук".utf8).write(to: dest)
        made.append(dest)

        XCTAssertNil(Inbox.pendingDest(for: queued), "без метки файл ещё не копировался")
        try Inbox.markPending(queued, dest: dest)
        made.append(Inbox.pendingMark(for: queued))
        XCTAssertEqual(Inbox.pendingDest(for: queued)?.path, dest.path)
        XCTAssertEqual(Inbox.pendingMark(for: queued).pathExtension, "sent", "метка — не запись: расширение вне audioExts")

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

    func testPendingVerdictTable() {
        // копия при ошибке НЕ удаляется и не перекопируется: файл держится в очереди
        XCTAssertEqual(Inbox.pendingVerdict(.uploaded), .retire)
        XCTAssertEqual(Inbox.pendingVerdict(.unknown), .retire, "ключей нет — судить нечем, принято")
        XCTAssertEqual(Inbox.pendingVerdict(.uploading), .wait)
        XCTAssertEqual(Inbox.pendingVerdict(.failed("квота")), .keep)
    }
}
