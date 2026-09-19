import XCTest
@testable import CharoiteiOS

/// №200: причина остановки записи — значение рядом с файлом, а не строка на
/// экране. Манифест `<файл>.json` едет на Mac парой с аудио; сирота без
/// манифеста получает `no_stop` — факт «стопа не было», не догадка.
final class StopRecordTests: XCTestCase {
    private let fm = FileManager.default
    private var base: URL!

    override func setUpWithError() throws {
        base = fm.temporaryDirectory.appendingPathComponent("stoprecord_\(UUID().uuidString.prefix(6))")
        try fm.createDirectory(at: base, withIntermediateDirectories: true)
    }

    override func tearDown() {
        try? fm.removeItem(at: base)
        super.tearDown()
    }

    /// Имя манифеста — контракт с импортом на Mac (`phone_manifest`): расширение
    /// ДОБАВЛЯЕТСЯ к имени аудио, чтобы `X.caf` и `X.m4a` не делили один JSON.
    func testSidecarNameIsAudioNamePlusJSON() {
        let audio = base.appendingPathComponent("iphone_2026-09-07_190237.caf")
        XCTAssertEqual(Inbox.sidecar(for: audio).lastPathComponent, "iphone_2026-09-07_190237.caf.json")
    }

    func testRecordRoundTripsWithStableKeysAndRawReasons() throws {
        let audio = base.appendingPathComponent("a.caf")
        let at = Date(timeIntervalSince1970: 1_788_796_957)   // 2026-09-07T16:02:37Z
        let rec = Recorder.StopRecord(kind: .rotate, reason: .callNoResume, at: at,
                                      seconds: 1200.5, series: "iphone_2026-09-07_190237")
        try rec.write(nextTo: audio)
        let raw = try String(contentsOf: Inbox.sidecar(for: audio), encoding: .utf8)
        // сырые значения — те, что читает Mac (channel_trace.PHONE_REASONS)
        XCTAssertTrue(raw.contains("\"reason\":\"call_no_resume\""), raw)
        XCTAssertTrue(raw.contains("\"kind\":\"rotate\""), raw)
        XCTAssertTrue(raw.contains("\"at\":\"2026-09-07T16:02:37Z\""), raw)
        XCTAssertTrue(raw.contains("\"version\":1"), raw)
        XCTAssertFalse(raw.contains("finalized_ok"), "неизвестное — не пишется, а не пишется как null")
        let back = Recorder.StopRecord.read(nextTo: audio)
        XCTAssertEqual(back, rec)
        XCTAssertEqual(Set(Recorder.StopReason.allCases.map(\.rawValue)),
                       ["user", "call_no_resume", "media_reset", "encode_error", "stalled", "no_stop"],
                       "список закрыт: причина существует только вместе с точкой кода, которая её ставит")
    }

    func testEveryReasonHasHumanTextAndUserIsNotShownAsProblem() {
        for reason in Recorder.StopReason.allCases {
            XCTAssertFalse(reason.text.isEmpty, "\(reason)")
        }
        XCTAssertNotEqual(Recorder.StopReason.callNoResume.text, Recorder.StopReason.stalled.text)
    }

    /// Сирота в current/: манифеста нет — «стопа не было» со временем последнего
    /// кадра (mtime); манифест, написанный stop() до гибели процесса, НЕ
    /// перезаписывается (Important GLM входного круга); пара едет вместе.
    func testRescueWritesNoStopOnlyWhenThereIsNoManifestAndMovesThePair() throws {
        let current = base.appendingPathComponent("current"), queue = base.appendingPathComponent("outbox")
        try fm.createDirectory(at: current, withIntermediateDirectories: true)
        try fm.createDirectory(at: queue, withIntermediateDirectories: true)

        let orphan = current.appendingPathComponent("orphan.caf")
        try Data(repeating: 1, count: Inbox.orphanMinBytes).write(to: orphan)
        let lastFrame = Date(timeIntervalSince1970: 1_757_000_000)
        try fm.setAttributes([.modificationDate: lastFrame], ofItemAtPath: orphan.path)

        let closed = current.appendingPathComponent("closed.caf")
        try Data(repeating: 1, count: Inbox.orphanMinBytes).write(to: closed)
        // целые секунды: ISO 8601 в манифесте дробную часть не хранит
        let honest = Recorder.StopRecord(kind: .stop, reason: .user, at: Date(timeIntervalSince1970: 1_788_000_000),
                                         seconds: 30, series: "closed")
        try honest.write(nextTo: closed)

        let stub = current.appendingPathComponent("stub.caf")
        try Data(repeating: 1, count: 10).write(to: stub)
        try Recorder.StopRecord(kind: .rotate, reason: .encodeError, at: Date(), seconds: 0.2, series: "s")
            .write(nextTo: stub)

        Inbox.rescueOrphans(from: current, to: queue)

        XCTAssertEqual(try fm.contentsOfDirectory(atPath: current.path), [], "пары уехали, огрызок удалён парой")
        let queued = Set(try fm.contentsOfDirectory(atPath: queue.path))
        XCTAssertEqual(queued, ["orphan.caf", "orphan.caf.json", "closed.caf", "closed.caf.json"])
        let orphanRec = Recorder.StopRecord.read(nextTo: queue.appendingPathComponent("orphan.caf"))
        XCTAssertEqual(orphanRec?.reason, .noStop)
        XCTAssertEqual(orphanRec?.kind, .stop)
        XCTAssertEqual(orphanRec?.at.timeIntervalSince1970 ?? 0, lastFrame.timeIntervalSince1970, accuracy: 1,
                       "время сироты — последний записанный кадр, не момент спасения")
        XCTAssertNil(orphanRec?.seconds, "длительность сироты неизвестна — не выдумываем")
        XCTAssertEqual(Recorder.StopRecord.read(nextTo: queue.appendingPathComponent("closed.caf")), honest,
                       "честная причина stop() пережила спасение")
    }

    /// Делегат финализации помечает манифест СВОЕГО файла, где бы тот ни лежал,
    /// а не текущего рекордера (Critical DS входного круга).
    func testMarkUnfinalizedFindsItsOwnFileInTheQueue() throws {
        let queue = base.appendingPathComponent("queue")
        try fm.createDirectory(at: queue, withIntermediateDirectories: true)
        let moved = queue.appendingPathComponent("old.caf")
        try Data(repeating: 1, count: 2048).write(to: moved)
        try Recorder.StopRecord(kind: .rotate, reason: .mediaReset, at: Date(), seconds: 100, series: "old")
            .write(nextTo: moved)
        // делегат знает URL, под которым файл писался — в другой папке
        let original = base.appendingPathComponent("current").appendingPathComponent("old.caf")
        Recorder.StopRecord.markUnfinalized(audio: original, searching: [queue])
        XCTAssertEqual(Recorder.StopRecord.read(nextTo: moved)?.finalizedOK, false)
        // чужого файла с тем же именем нет — ничего не создано
        XCTAssertFalse(fm.fileExists(atPath: Inbox.sidecar(for: original).path))
    }
}
