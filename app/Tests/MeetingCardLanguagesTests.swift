import XCTest
@testable import CharoiteApp

/// Карточка встречи читает то, что лежит на диске, а лежать там может встреча
/// на любом из трёх языков.
///
/// Разбор был русским: у английской встречи «Решили» и «Поручения» оставались
/// пустыми при том, что в Саммари.md они есть — карточка показывала пустоту
/// поверх записанных решений. Язык берётся из документа, а не из настроек:
/// в архиве соседствуют встречи до и после переключения `sufler.language`.
final class MeetingCardLanguagesTests: XCTestCase {

    private let russian = """
    **Суть одной строкой:** договорились по провайдеру.

    ## Решили
    - **YuPay** — берём, комиссия 2.8%

    ## Поручения
    - **Мария** — договор до 22.07

    ## Открытые вопросы
    - кто платит за интеграцию
    """

    private let english = """
    **Bottom line:** the provider is picked.

    ## Decisions
    - **YuPay** — chosen, 2.8% fee

    ## Action items
    - **Maria** — contract by 22.07

    ## Open questions
    - who pays for the integration
    """

    private let chinese = """
    **一句话概括：** 已确定支付服务商。

    ## 决定
    - **YuPay** — 选定，费率 2.8%

    ## 任务
    - **玛丽亚** — 7月22日前签合同

    ## 待解决问题
    - 集成费用由谁承担
    """

    func testGistIsReadInEveryLanguage() {
        XCTAssertEqual(MeetingCardLoader.gist(fromSummary: russian), "договорились по провайдеру.")
        XCTAssertEqual(MeetingCardLoader.gist(fromSummary: english), "the provider is picked.")
        XCTAssertEqual(MeetingCardLoader.gist(fromSummary: chinese), "已确定支付服务商。")
        XCTAssertNil(MeetingCardLoader.gist(fromSummary: "саммари без маркера"))
    }

    func testSectionsAreReadInEveryLanguage() {
        for (text, decision, task, question) in [
            (russian, "**YuPay** — берём, комиссия 2.8%", "**Мария** — договор до 22.07",
             "кто платит за интеграцию"),
            (english, "**YuPay** — chosen, 2.8% fee", "**Maria** — contract by 22.07",
             "who pays for the integration"),
            (chinese, "**YuPay** — 选定，费率 2.8%", "**玛丽亚** — 7月22日前签合同",
             "集成费用由谁承担"),
        ] {
            XCTAssertEqual(MeetingCardLoader.items(inSections: MeetingCardLoader.Section.decisions,
                                             of: text), [decision])
            XCTAssertEqual(MeetingCardLoader.items(inSections: MeetingCardLoader.Section.tasks,
                                             of: text), [task])
            XCTAssertEqual(MeetingCardLoader.items(inSections: MeetingCardLoader.Section.questions,
                                             of: text), [question])
        }
    }

    /// Пустой раздел — это честный ответ модели, а не пункт списка.
    func testEmptySectionIsNotAnItem() {
        XCTAssertTrue(MeetingCardLoader.items(
            inSections: MeetingCardLoader.Section.decisions,
            of: "## Решили\n- решений не было\n").isEmpty)
        XCTAssertTrue(MeetingCardLoader.items(
            inSections: MeetingCardLoader.Section.decisions,
            of: "## Decisions\n- no decisions were made\n").isEmpty)
        XCTAssertTrue(MeetingCardLoader.items(
            inSections: MeetingCardLoader.Section.decisions,
            of: "## 决定\n- 没有做出决定\n").isEmpty)
    }

    /// Историческое написание раздела решений в архиве.
    func testHistoricRussianSpellingStillReads() {
        XCTAssertEqual(MeetingCardLoader.items(inSections: MeetingCardLoader.Section.decisions,
                                         of: "## Решения\n- **YuPay** — берём\n"),
                       ["**YuPay** — берём"])
    }

    /// Минутки на китайском разбирались в ноль: заголовки в парсере были
    /// только русские и английские.
    func testChineseMinutesAreParsed() {
        let minutes = MeetingMinutes.parse("""
        # 会议纪要
        **日期/时间：** 2026-08-08 **参会人：** 伊万、玛丽亚

        ## 议题
        - 支付服务商

        ## 决定
        - 选定 YuPay

        ## 行动项
        - [ ] **玛丽亚** — 签合同 — 7月22日前

        ## 待解决问题
        - 集成费用由谁承担

        ## 风险
        - 认证可能延期
        """)
        XCTAssertEqual(minutes.topics.map(\.text), ["支付服务商"])
        XCTAssertEqual(minutes.decisions.map(\.text), ["选定 YuPay"])
        XCTAssertEqual(minutes.tasks.count, 1)
        XCTAssertEqual(minutes.openQuestions.map(\.text), ["集成费用由谁承担"])
        XCTAssertEqual(minutes.risks.map(\.text), ["认证可能延期"])
        XCTAssertFalse(minutes.topics.contains { $0.text.contains("伊万") },
                       "участники не должны утечь в темы")
    }
}
