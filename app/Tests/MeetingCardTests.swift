import XCTest
@testable import CharoiteApp

/// Карточка встречи собирается из файлов, которые уже лежат на диске.
/// Здесь — парсеры: они чистые, и падать им положено в тестах, а не на
/// открытой карточке во время рабочего дня.
final class MeetingCardTests: XCTestCase {
    func testPortableManifestUsesLanguageIndependentKeys() throws {
        let folder = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: folder) }
        let json = """
        {
          "schema_version": 1,
          "meeting_id": "2026-08-03_1130",
          "title": "Planning",
          "duration_minutes": 75,
          "participants": ["Ivan", "Maria"],
          "summary": "Plan accepted",
          "decisions": ["Ship Friday"],
          "action_items": ["Maria — verify build"],
          "open_questions": ["Android scope?"],
          "files": {}
        }
        """
        try json.write(to: folder.appendingPathComponent("meeting.meta.json"),
                       atomically: true, encoding: .utf8)

        let manifest = try XCTUnwrap(MeetingCardLoader.manifest(in: folder))
        XCTAssertEqual(manifest.title, "Planning")
        XCTAssertEqual(manifest.durationMinutes, 75)
        XCTAssertEqual(manifest.actionItems, ["Maria — verify build"])
        XCTAssertEqual(MeetingCardLoader.durationText(minutes: 75),
                       L.t("1 ч 15 мин", "1 h 15 min", "1 小时 15 分"))
    }
    // MARK: стенограмма

    private let transcript = """
    # Встреча 2026-08-03_113012
    Участники (звучали в разговоре): Мария, Пётр, Анна Иванова

    **Ведущий** [11:32]:
    Привет.

    **Собеседник 2** [11:36–11:43]:
    Длинная реплика.

    **Собеседник 1** [12:10]:
    Последняя.
    """

    func testParticipantsComeFromTheHeaderLine() {
        XCTAssertEqual(MeetingCardLoader.participants(fromTranscript: transcript),
                       ["Мария", "Пётр", "Анна Иванова"])
    }

    func testNoHeaderNoParticipants() {
        XCTAssertEqual(MeetingCardLoader.participants(fromTranscript: "просто текст"), [])
    }

    func testDurationSpansFirstToLastTimestamp() {
        // 11:32 → 12:10 — 38 минут; диапазон [11:36–11:43] даёт обе точки
        XCTAssertEqual(MeetingCardLoader.durationText(fromTranscript: transcript),
                       "38 мин")
    }

    func testMidnightMeetingDoesNotLastMinusADay() {
        let night = "**А** [23:58]:\nраз\n\n**Б** [00:05]:\nдва"
        XCTAssertEqual(MeetingCardLoader.durationText(fromTranscript: night), "7 мин")
    }

    func testSingleTimestampGivesNoDuration() {
        XCTAssertNil(MeetingCardLoader.durationText(fromTranscript: "**А** [11:32]:\nраз"))
    }

    func testLongMeetingShowsHours() {
        let long = "**А** [10:00]:\nраз\n\n**Б** [11:05]:\nдва"
        XCTAssertEqual(MeetingCardLoader.durationText(fromTranscript: long), "1 ч 05 мин")
    }

    // MARK: саммари

    private let summary = """
    ---
    type: саммари
    ---

    # Саммари — 2026-08-03 11-30 — Инцидент загрузки

    **Суть одной строкой:** Инцидент разобран, поток восстановлен.

    ## О чём говорили
    - **Тема** — что по ней.

    ## Решили
    - **Механизм** — чинить механизм, не генератор.
    - **Авария** — поднять при затягивании сроков.

    ## Поручения
    - **Мария** — прислать письмо с оценкой — до пятницы.

    ---
    Подробнее: [[Минутки]]
    """

    func testGistIsTheOneLiner() {
        XCTAssertEqual(MeetingCardLoader.gist(fromSummary: summary),
                       "Инцидент разобран, поток восстановлен.")
    }

    func testDecisionsAndTasksAreSeparateSections() {
        XCTAssertEqual(MeetingCardLoader.items(inSection: "Решили", of: summary).count, 2)
        XCTAssertEqual(MeetingCardLoader.items(inSection: "Поручения", of: summary),
                       ["**Мария** — прислать письмо с оценкой — до пятницы."])
    }

    func testHonestlyEmptySectionGivesNoItems() {
        // «решений не было» — состояние, а не пункт списка
        let empty = "## Решили\nрешений не было\n\n## Поручения\n- дело\n"
        XCTAssertEqual(MeetingCardLoader.items(inSection: "Решили", of: empty), [])
    }

    func testMissingSectionGivesNoItems() {
        XCTAssertEqual(MeetingCardLoader.items(inSection: "Решили", of: "# пусто"), [])
    }

    // MARK: куда вести

    func testArchiveFolderIsFoundByStampNotByTitle() throws {
        // тема в имени папки меняется переименованием — якорь только дата-время
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
        let archive = tmp.appendingPathComponent("Встречи-архив")
        let folder = archive.appendingPathComponent("2026-08-03 11-30 — Инцидент загрузки")
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmp) }

        // meetingID с секундами — первые 15 знаков дают тот же штамп
        let found = MeetingCardLoader.archiveFolder(graph: tmp, stamp: "2026-08-03_1130")
        XCTAssertEqual(found?.lastPathComponent, "2026-08-03 11-30 — Инцидент загрузки")
    }

    func testObsidianURLPointsAtTheGraphNote() throws {
        let note = URL(fileURLWithPath:
            "/Users/x/iCloud/Documents/Проект_Альфа/Встречи/2026-08-03_1130.md")
        let url = try XCTUnwrap(MeetingCardLoader.obsidianURL(noteURL: note))

        XCTAssertEqual(url.scheme, "obsidian")
        let comps = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false))
        let q = Dictionary(uniqueKeysWithValues: comps.queryItems!.map { ($0.name, $0.value ?? "") })
        XCTAssertEqual(q["vault"], "Documents")
        XCTAssertEqual(q["file"], "Проект_Альфа/Встречи/2026-08-03_1130")
    }

    // MARK: буфер обмена

    func testFullTextCarriesEverythingAMailNeeds() {
        var card = MeetingCard()
        card.gist = "Суть."
        card.decisions = ["**Раз** — решение."]
        card.tasks = ["**Игорь** — дело — срок."]
        card.participants = ["Мария", "Пётр"]
        card.durationText = "38 мин"

        let text = MeetingCardLoader.fullText(title: "Инцидент загрузки",
                                              dateText: "3 августа 11:30", card: card)
        for needle in ["Инцидент загрузки", "38 мин", "Мария, Пётр", "Суть.",
                       "решение", "дело — срок"] {
            XCTAssertTrue(text.contains(needle), "в письме нет: \(needle)")
        }
    }

    func testTasksTextIsJustTheTasks() {
        var card = MeetingCard()
        card.tasks = ["дело один", "дело два"]
        XCTAssertEqual(MeetingCardLoader.tasksText(card: card), "- дело один\n- дело два")
    }

    // MARK: лог облачной ревизии

    func testCloudReviewTakesTheLastRun() {
        // в логе три прогона: правдой считается последний
        let log = """
        [cloud-review] 2026-08-03_1130: файлов в запросе 3, режим правка графа
        [cloud-review] ревизия НЕ сохранена (код -1, 0 знаков) — см. x.partial
        [cloud-review] правок графа: 83, откатано запрещённых: Саммари.md
        [cloud-review] 2026-08-03_1130: файлов в запросе 4, режим правка графа
        [cloud-review] ревизия сохранена: 2026-08-03_1130_Тема_ревизия_claude.md
        [cloud-review] правок графа: 103, откатано запрещённых: Граф.md
        """
        let r = MeetingCardLoader.cloudReview(fromLog: log)
        XCTAssertEqual(r, CloudReviewResult(edits: 103, saved: true))
    }

    func testCloudReviewUnsavedRevisionIsHonest() {
        let log = """
        [cloud-review] таймаут 1800с — разбор прерван
        [cloud-review] ревизия НЕ сохранена (код -1, 0 знаков) — см. x.partial
        [cloud-review] правок графа: 83, откатано запрещённых: Саммари.md
        """
        let r = MeetingCardLoader.cloudReview(fromLog: log)
        XCTAssertEqual(r, CloudReviewResult(edits: 83, saved: false))
    }

    func testCloudReviewWithoutEditsLineIsNil() {
        // ревизия ещё идёт или упала до разбора — итога нет
        let log = "[cloud-review] 2026-08-03_1130: файлов в запросе 3\n"
        XCTAssertNil(MeetingCardLoader.cloudReview(fromLog: log))
        XCTAssertNil(MeetingCardLoader.cloudReview(fromLog: ""))
    }

    // MARK: команда переименования

    func testRenameCommandUsesVenvPythonAndShortStamp() {
        let cmd = MeetingRenameCommand.build(
            root: URL(fileURLWithPath: "/opt/ch"),
            meetingID: "2026-08-03_113012", title: "Инцидент загрузки")

        XCTAssertTrue(cmd.exec.path.hasSuffix(".venv/bin/python"))
        XCTAssertEqual(cmd.args.first?.hasSuffix("scripts/rename_meeting.py"), true)
        XCTAssertTrue(cmd.args.contains("2026-08-03_1130"),
                      "скрипту уходит штамп без секунд — им названы файлы темы")
        XCTAssertTrue(cmd.args.contains("--yes"))
    }
}
