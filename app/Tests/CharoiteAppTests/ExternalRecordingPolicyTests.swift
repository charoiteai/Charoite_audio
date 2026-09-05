import XCTest
@testable import CharoiteApp

/// Вкладка «Внешняя запись» (№166): срок удаления, подписи и порядок —
/// правила держит тест, а не глаз на живой папке.
final class ExternalRecordingPolicyTests: XCTestCase {
    private func item(_ name: String, phase: ImportItem.Phase, recorded: Date = Date()) -> ImportItem {
        ImportItem(url: URL(fileURLWithPath: "/tmp/inbox/\(name)"), name: name,
                   bytes: 10, recorded: recorded, phase: phase)
    }

    private func imported(at importedAt: Date, deleteAt: Date, stamp: String? = "2026-09-05_1200") -> ImportItem.Imported {
        .init(stamp: stamp, transcript: "/t.md", kind: "meeting", importedAt: importedAt,
              deleteAt: deleteAt, isRepeat: false, noSpeech: false)
    }

    /// Сайдкар скрипта: срок берётся из delete_after, а не считается заново.
    func testСайдкарОтдаётСрокУдаленияКакЕстьИКлючиСкрипта() throws {
        let json = """
        {"imported_at": 1000, "delete_after": 5000, "keep_days": 2, "stamp": "2026-09-05_1200",
         "transcript": "/t/2026-09-05_1200.md", "kind": "meeting", "repeat": false, "no_speech": true}
        """
        let sidecar = try JSONDecoder().decode(ExternalRecordingPolicy.Sidecar.self, from: Data(json.utf8))
        let done = ExternalRecordingPolicy.imported(from: sidecar)
        XCTAssertEqual(done.importedAt, Date(timeIntervalSince1970: 1000))
        XCTAssertEqual(done.deleteAt, Date(timeIntervalSince1970: 5000))
        XCTAssertEqual(done.stamp, "2026-09-05_1200")
        XCTAssertTrue(done.noSpeech)
        XCTAssertFalse(done.isRepeat)
    }

    /// Сайдкар без delete_after (или старый): срок — импорт плюс keep_days.
    func testБезСрокаВСайдкареСчитаемОтИмпорта() throws {
        let sidecar = try JSONDecoder().decode(ExternalRecordingPolicy.Sidecar.self,
                                               from: Data(#"{"imported_at": 1000}"#.utf8))
        let done = ExternalRecordingPolicy.imported(from: sidecar)
        XCTAssertEqual(done.deleteAt, Date(timeIntervalSince1970: 1000 + 2 * 86400))
        XCTAssertEqual(done.kind, "meeting")
    }

    /// Копия без сайдкара срок не выдумывает: его назначит первая уборка
    /// скрипта (сайдкар «увидели сейчас»), а не ctime и не mtime.
    func testЛегасиКопияБезДатыДоПервойУборки() {
        let text = ExternalRecordingPolicy.statusText(.legacy, now: Date())
        XCTAssertTrue(text.contains(L.t("ближайшая проверка", "next check", "下次检查")), text)
        XCTAssertFalse(text.contains("удалится "), "даты, которую никто не назначал, быть не должно")
    }

    /// Порядок: сначала то, что требует человека (сбой), потом очередь,
    /// потом сделанное — свежие импорты сверху.
    func testСбойныеСверхуЗатемОчередьЗатемГотовые() {
        let now = Date()
        let failed = item("bad.m4a", phase: .failed(message: "x"), recorded: now.addingTimeInterval(-9999))
        let waiting = item("new.m4a", phase: .waiting, recorded: now)
        let older = item("old.m4a", phase: .done(imported(at: now.addingTimeInterval(-3600), deleteAt: now)))
        let newer = item("fresh.m4a", phase: .done(imported(at: now, deleteAt: now)))
        let sorted = ExternalRecordingPolicy.sorted([older, waiting, newer, failed])
        XCTAssertEqual(sorted.map(\.name), ["bad.m4a", "new.m4a", "fresh.m4a", "old.m4a"])
        XCTAssertEqual(ExternalRecordingPolicy.failedCount(sorted), 1)
    }

    func testПодписьСрокаГоворитДатойСегодняИлиСледующейПроверкой() {
        let now = Date()
        XCTAssertTrue(ExternalRecordingPolicy.deletionText(deleteAt: now.addingTimeInterval(-1), now: now)
            .contains(L.t("следующей проверке", "next check", "下次检查")))
        // «сегодня» — только если тот же календарный день
        let laterToday = Calendar.current.date(bySettingHour: 23, minute: 59, second: 0, of: now)!
        if laterToday > now {
            XCTAssertTrue(ExternalRecordingPolicy.deletionText(deleteAt: laterToday, now: now)
                .contains(L.t("сегодня", "today", "今天")))
        }
        let inThreeDays = now.addingTimeInterval(3 * 86400)
        let text = ExternalRecordingPolicy.deletionText(deleteAt: inThreeDays, now: now)
        let f = DateFormatter()
        f.locale = L.locale
        f.dateFormat = "dd.MM"
        XCTAssertTrue(text.contains(f.string(from: inThreeDays)), text)
    }

    func testПодписьСостоянияНесётШтампИСрок() {
        let now = Date()
        let done = imported(at: now, deleteAt: now.addingTimeInterval(3 * 86400))
        let text = ExternalRecordingPolicy.statusText(.done(done), now: now)
        XCTAssertTrue(text.contains("2026-09-05_1200"), text)
        XCTAssertTrue(text.contains(L.t("удалится", "deleted", "删除")), text)
        let failed = ExternalRecordingPolicy.statusText(.failed(message: "транскрибация не удалась"), now: now)
        XCTAssertTrue(failed.contains("транскрибация не удалась"), failed)
    }

    /// Диктофон телефона всё зовёт Recording.m4a — чужую копию не затираем.
    func testСвободноеИмяНеЗатираетЗанятое() {
        XCTAssertEqual(ExternalRecordingPolicy.uniqueName("Recording.m4a", taken: []), "Recording.m4a")
        XCTAssertEqual(ExternalRecordingPolicy.uniqueName("Recording.m4a", taken: ["Recording.m4a"]),
                       "Recording-1.m4a")
        XCTAssertEqual(ExternalRecordingPolicy.uniqueName("Recording.m4a",
                                                          taken: ["Recording.m4a", "Recording-1.m4a"]),
                       "Recording-2.m4a")
        XCTAssertEqual(ExternalRecordingPolicy.uniqueName("notes", taken: ["notes"]), "notes-1")
    }

    func testПоддерживаемыеФорматыЗеркалятСкрипт() {
        XCTAssertTrue(ExternalRecordingPolicy.isSupported(URL(fileURLWithPath: "/x/a.M4A")))
        XCTAssertTrue(ExternalRecordingPolicy.isSupported(URL(fileURLWithPath: "/x/zoom.vtt")))
        XCTAssertFalse(ExternalRecordingPolicy.isSupported(URL(fileURLWithPath: "/x/clip.mov")))
    }
}
