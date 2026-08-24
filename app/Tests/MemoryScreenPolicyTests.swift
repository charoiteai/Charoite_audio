import XCTest
@testable import CharoiteApp

#if os(macOS)
/// Экран «Память» по макету MOBILE_2026-08: источники из блока поиска и
/// строка происхождения.
final class MemoryScreenPolicyTests: XCTestCase {

    func testSourcesParseLocalBulletsAndBrainMentions() {
        let block = """
        • Встречи/2026-08-21_1202_Перенос_встречи.md
          …Мария Соколова в отпуске…

        • Люди/Мария Соколова.md
          …руководит одной из команд…

        Из brain: см. [Ядра/Проблема с сертификатом.md] и Досье/Подпись релиза.md,
        а также Встречи/2026-08-21_1202_Перенос_встречи.md ещё раз (дубль).
        """
        let s = MemoryScreenPolicy.sources(from: block)
        XCTAssertEqual(s.map(\.rel), [
            "Встречи/2026-08-21_1202_Перенос_встречи.md",
            "Люди/Мария Соколова.md",
            "Ядра/Проблема с сертификатом.md",
            "Досье/Подпись релиза.md",
        ], "порядок поиска сохранён, дубль схлопнут, пути с пробелами целы")
        XCTAssertEqual(s.map(\.kind), [.meeting, .node, .node, .dossier])
    }

    func testSourcesEmptyAndLimit() {
        XCTAssertTrue(MemoryScreenPolicy.sources(from: "").isEmpty)
        XCTAssertTrue(MemoryScreenPolicy.sources(from: "просто текст без путей").isEmpty)
        let many = (1...9).map { "• Люди/Человек \($0).md" }.joined(separator: "\n")
        XCTAssertEqual(MemoryScreenPolicy.sources(from: many).count, 6, "потолок чипов")
    }

    func testMeetingLabelAndTitles() {
        XCTAssertEqual(MemoryScreenPolicy.meetingLabel(stem: "2026-08-21_1202_Перенос_встречи"),
                       "Встреча 21.08 · Перенос встречи")
        XCTAssertEqual(MemoryScreenPolicy.meetingLabel(stem: "2026-08-24_101457"),
                       "Встреча 24.08", "секундный штамп без темы — только дата")
        XCTAssertNil(MemoryScreenPolicy.meetingLabel(stem: "не встреча"))
        let node = MemoryScreenPolicy.Source(rel: "Системы/ВВКИ.md", kind: .node)
        XCTAssertEqual(node.title, "Узел · ВВКИ")
    }

    func testMetaLineVariants() {
        XCTAssertEqual(
            MemoryScreenPolicy.metaLine(model: "qwen3.6:35b-a3b", seconds: 12,
                                        meetingsInContext: 2, memoryOn: true, weakMatches: false),
            "локально · qwen3.6:35b-a3b · 12 с · 2 встречи в контексте")
        XCTAssertTrue(MemoryScreenPolicy.metaLine(model: "m", seconds: 3, meetingsInContext: 0,
                                                  memoryOn: false, weakMatches: false)
            .hasSuffix("память выключена"))
        XCTAssertTrue(MemoryScreenPolicy.metaLine(model: "m", seconds: 3, meetingsInContext: 0,
                                                  memoryOn: true, weakMatches: true)
            .hasSuffix("граф: слабые совпадения"))
        XCTAssertTrue(MemoryScreenPolicy.metaLine(model: "m", seconds: 3, meetingsInContext: 0,
                                                  memoryOn: true, weakMatches: false)
            .hasSuffix("граф не в контексте"))
    }

    // Круг-1 по PR #396: ветка «источники без встреч» (DS Critical),
    // граница матчинга пар одной минуты, скобки в пути, контракт en/zh.

    func testMetaLineNodesOnlyCountsSources() {
        let line = MemoryScreenPolicy.metaLine(
            model: "qwen", seconds: 3, meetingsInContext: 0,
            sourcesInContext: 2, memoryOn: true, weakMatches: false)
        XCTAssertTrue(line.contains("2 источника в контексте"), line)
        XCTAssertFalse(line.contains("граф не в контексте"), line)
        let empty = MemoryScreenPolicy.metaLine(
            model: "qwen", seconds: 3, meetingsInContext: 0,
            sourcesInContext: 0, memoryOn: true, weakMatches: false)
        XCTAssertTrue(empty.contains("граф не в контексте"), empty)
    }

    func testMatchRecordPrefersExactStampOverMinutePrefix() {
        // Пара «одной минуты» (#388): владельцу минутный штамп, соседке
        // секундный. Голый префикс отдавал чужую карточку.
        let ids = ["2026-08-21_120245", "2026-08-21_1202"]
        XCTAssertEqual(MemoryScreenPolicy.matchRecord(stem: "2026-08-21_1202", ids: ids),
                       "2026-08-21_1202")
        XCTAssertEqual(MemoryScreenPolicy.matchRecord(stem: "2026-08-21_120245", ids: ids),
                       "2026-08-21_120245")
        // Тема в стеме не мешает точному штампу.
        XCTAssertEqual(MemoryScreenPolicy.matchRecord(stem: "2026-08-21_1202_Тема встречи", ids: ids),
                       "2026-08-21_1202")
        // Архивный суффикс «-2» из #388 тоже каноничен по штампу.
        XCTAssertEqual(MemoryScreenPolicy.matchRecord(stem: "2026-08-21_120245-2_Тема", ids: ids),
                       "2026-08-21_120245")
        // Чужая минута — не матч.
        XCTAssertNil(MemoryScreenPolicy.matchRecord(stem: "2026-08-21_1203", ids: ids))
    }

    func testSourcesKeepParenthesesAndHonorLimitZero() {
        let block = "• Люди/Анна (эксперт).md\n  …профиль…\n• Встречи/2026-08-21_1202_Тема (важно).md\n  …решения…"
        let got = MemoryScreenPolicy.sources(from: block)
        XCTAssertEqual(got.map(\.rel),
                       ["Люди/Анна (эксперт).md", "Встречи/2026-08-21_1202_Тема (важно).md"])
        XCTAssertTrue(MemoryScreenPolicy.sources(from: block, limit: 0).isEmpty)
    }

    func testGraphContractCoversEnglishAndChineseFolders() {
        XCTAssertEqual(MemoryScreenPolicy.kind(of: "Meetings/2026-08-21_1202.md"), .meeting)
        XCTAssertEqual(MemoryScreenPolicy.kind(of: "People/Anna.md"), .node)
        XCTAssertEqual(MemoryScreenPolicy.kind(of: "Cores/Release.md"), .node)
        XCTAssertEqual(MemoryScreenPolicy.kind(of: "会议/2026-08-21_1202.md"), .meeting)
        XCTAssertEqual(MemoryScreenPolicy.kind(of: "人物/王伟.md"), .node)
        XCTAssertEqual(MemoryScreenPolicy.kind(of: "Встречи-архив/2026-07-01_1000.md"), .meeting)
        XCTAssertEqual(MemoryScreenPolicy.kind(of: "Dossiers/Signing.md"), .dossier)
        XCTAssertEqual(MemoryScreenPolicy.kind(of: "Документация/Отчёт.md"), .doc)
        XCTAssertEqual(MemoryScreenPolicy.kind(of: "Неизвестное/файл.md"), .doc)
    }
}
#endif
