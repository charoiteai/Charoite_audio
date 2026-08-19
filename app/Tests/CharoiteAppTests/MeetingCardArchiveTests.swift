import XCTest
@testable import CharoiteApp

/// Карточка встречи без заметки графа: чей это архив.
///
/// Когда граф выключен профилем, заметки нет, и папку архива карточка ищет
/// в настроенном graph_dir по «дата чч-мм». Сменил человек graph_dir — там
/// может лежать чужая встреча тех же минут, и показать её решения нельзя.
/// Первая версия сверки сравнивала `meeting_id` из манифеста (минутный
/// штамп) со снимком статуса (посекундный) — условие было ложным всегда, и
/// карточка теряла СВОЙ архив в каждом таком случае (третий круг, DeepSeek).
final class MeetingCardArchiveTests: XCTestCase {

    private func folder(withTranscript text: String?) throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("card-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        if let text {
            try text.write(to: dir.appendingPathComponent("Стенограмма.md"),
                           atomically: true, encoding: .utf8)
        }
        return dir
    }

    func testСвойАрхивОстаётсяСКарточкой() throws {
        let text = "- Коля: начали\n- Оля: по релизу всё\n"
        let dir = try folder(withTranscript: text)
        defer { try? FileManager.default.removeItem(at: dir) }
        XCTAssertTrue(MeetingCardLoader.isOurArchive(dir, text))
    }

    func testХвостСтенограммыМожетРастиПослеАрхивации() throws {
        let archived = "- Коля: начали\n- Оля: по релизу всё\n"
        let dir = try folder(withTranscript: archived)
        defer { try? FileManager.default.removeItem(at: dir) }
        XCTAssertTrue(MeetingCardLoader.isOurArchive(dir, archived + "- Коля: и ещё вот\n"),
                      "сверяем начало: правка хвоста не должна прятать разбор")
    }

    func testЧужаяВстречаТехЖеМинутОтсекается() throws {
        let dir = try folder(withTranscript: "- Ира: обсуждаем бюджет\n- Пётр: согласен\n")
        defer { try? FileManager.default.removeItem(at: dir) }
        XCTAssertFalse(MeetingCardLoader.isOurArchive(dir, "- Коля: начали\n- Оля: по релизу\n"))
    }

    func testПравкаЗаголовкаНеПрячетСвойАрхив() throws {
        // Шапку человек правит в Obsidian чаще всего; реплики после
        // архивации не меняются — сверяем по ним.
        let archived = "# Встреча 19.08\n- Коля: начали\n- Оля: по релизу всё\n"
        let dir = try folder(withTranscript: archived)
        defer { try? FileManager.default.removeItem(at: dir) }
        let edited = "# Планёрка по релизу 0.53\n\n- Коля: начали\n- Оля: по релизу всё\n"
        XCTAssertTrue(MeetingCardLoader.isOurArchive(dir, edited))
    }

    func testСомнениеТрактуетсяВПользуПоказа() throws {
        let empty = try folder(withTranscript: nil)
        defer { try? FileManager.default.removeItem(at: empty) }
        XCTAssertTrue(MeetingCardLoader.isOurArchive(empty, "- Коля: начали\n"),
                      "в архиве нет стенограммы — прятать разбор не за что")
        let dir = try folder(withTranscript: "- Коля: начали\n")
        defer { try? FileManager.default.removeItem(at: dir) }
        XCTAssertTrue(MeetingCardLoader.isOurArchive(dir, nil),
                      "своя стенограмма не прочиталась — сравнивать не с чем")
    }
}
