import XCTest
@testable import CharoiteApp

final class TopicCardTests: XCTestCase {
    private func graph() throws -> URL {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        return root
    }

    @discardableResult
    private func put(_ root: URL, _ rel: String, _ text: String) throws -> URL {
        let url = root.appendingPathComponent(rel)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try text.write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    func testDossierSectionsDoNotMixSourcesOrAuthorEditsWithDecisions() {
        let text = """
        ---
        собрано: 2026-10-10
        ---
        # Досье: Оплата
        ## Сейчас
        Выбрали Б [[Встречи/2026-10-09_1000]].
        ## Как пришли
        - 01.10: обсуждали А.
        - 09.10: выбрали Б.
        ## Решено
        Проверить сертификацию [[Проверка]].
        ## Источники
        [[Служебный список]]
        ## Правки автора
        Решение пока предварительное.
        """
        let parsed = TopicCard.parse(text)
        XCTAssertEqual(parsed.assembled, "2026-10-10")
        XCTAssertEqual(parsed.sections[.decisions], "Проверить сертификацию [[Проверка]].")
        XCTAssertEqual(parsed.sections[.author], "Решение пока предварительное.")
        XCTAssertNil(parsed.sections[.uncertainty])
        XCTAssertTrue(parsed.sections[.changes]?.contains("09.10") == true)
    }

    func testBodyRuleAndFencedHeadingsDoNotHideOrReclassifyText() {
        let parsed = TopicCard.parse("## Статус\r\n---\r\nБ\r\n```\r\n## Решено\r\nпример\r\n```\r\n## Следующий шаг\r\nПроверить")
        XCTAssertTrue(parsed.sections[.current]?.contains("## Решено") == true)
        XCTAssertNil(parsed.sections[.decisions])
        XCTAssertEqual(parsed.sections[.open], "Проверить")
    }

    func testEnglishCoreAndExplicitUncertainties() {
        let parsed = TopicCard.parse("## Status\nChosen B\n## History\nA → B\n## Next step\nCheck\n## Uncertainties\nTwo dates")
        XCTAssertEqual(parsed.sections[.current], "Chosen B")
        XCTAssertEqual(parsed.sections[.changes], "A → B")
        XCTAssertEqual(parsed.sections[.open], "Check")
        XCTAssertEqual(parsed.sections[.uncertainty], "Two dates")
    }

    func testWikiAliasesAnchorsAndDuplicateReferences() {
        XCTAssertEqual(TopicCard.linkTargets("[[Meetings/One|alias]] [[Two#Decision]] [[Two]]"),
                       ["Meetings/One", "Two"])
    }

    func testDossierPreferredAndEverySectionHasItsOwnSources() throws {
        let root = try graph()
        try put(root, "Ядра/Оплата.md", "## Статус\nСтарое")
        try put(root, "Досье/Оплата.md", "---\nсобрано: 2026-10-10\n---\n## Сейчас\nБ [[Встречи/Одна]]\n## Открыто\nСрок [[Нет файла]]")
        let meeting = try put(root, "Встречи/Одна.md", "Решение")
        let card = try TopicCard.load(graph: root, folder: "Ядра", name: "Оплата")
        XCTAssertTrue(card.isDossier)
        XCTAssertEqual(card.assembled, "2026-10-10")
        XCTAssertEqual(card.sources[.current]?.first?.url, meeting)
        XCTAssertNil(card.sources[.open]?.first?.url)
        XCTAssertEqual(card.sources[.open]?.first?.target, "Нет файла")
    }

    func testMissingOrAmbiguousDossierFallsBackToCoreWithoutInventingSections() throws {
        let root = try graph()
        try put(root, "Cores/Payment.md", "## Status\nChosen B\n## Next step\nRevisit")
        let first = try TopicCard.load(graph: root, folder: "Cores", name: "Payment")
        XCTAssertFalse(first.isDossier)
        XCTAssertNil(first.sections[.decisions])
        try put(root, "Dossiers/Payment.md", "## Сейчас\nA")
        try put(root, "Досье/Payment.md", "## Сейчас\nC")
        let second = try TopicCard.load(graph: root, folder: "Cores", name: "Payment")
        XCTAssertFalse(second.isDossier)
        XCTAssertEqual(second.sections[.current], "Chosen B")
    }

    func testUnreadableDossierDoesNotSilentlyShowOlderCore() throws {
        let root = try graph()
        try put(root, "Ядра/Тема.md", "## Статус\nстарое")
        let dossier = try put(root, "Досье/Тема.md", "")
        try Data([0xff, 0xfe]).write(to: dossier)
        XCTAssertThrowsError(try TopicCard.load(graph: root, folder: "Ядра", name: "Тема"))
    }

    func testAmbiguousBareSourceIsDisabledButQualifiedSourceResolves() throws {
        let root = try graph()
        let one = try put(root, "People/CRM.md", "один")
        let two = try put(root, "Systems/CRM.md", "два")
        XCTAssertNil(TopicCard.resolve("CRM", graph: root, files: [one, two]))
        XCTAssertEqual(TopicCard.resolve("Systems/CRM", graph: root, files: [one, two]), two)
    }

    func testUnsafeTargetsAndExternalSymlinksCannotResolveOrLoad() throws {
        let root = try graph()
        let outside = try graph()
        let secret = try put(outside, "secret.md", "private")
        let link = try put(root, "Cores/Escape.md", "")
        try FileManager.default.removeItem(at: link)
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: secret)
        XCTAssertNil(TopicCard.resolve("Escape", graph: root, files: [link]))
        XCTAssertThrowsError(try TopicCard.load(graph: root, folder: "Cores", name: "Escape"))
        for target in ["../secret", "/secret", "People/../secret", ".backup/secret", "https://host", "People\\secret"] {
            XCTAssertNil(TopicCard.resolve(target, graph: root, files: [secret]))
        }
        XCTAssertThrowsError(try TopicCard.load(graph: root, folder: "Cores", name: "../secret"))
    }

    func testClickTimeContainmentDetectsReplacedSource() throws {
        let root = try graph()
        let outside = try graph()
        let source = try put(root, "Meetings/One.md", "source")
        let secret = try put(outside, "secret.md", "private")
        XCTAssertTrue(TopicCard.isInside(source, graph: root))
        try FileManager.default.removeItem(at: source)
        try FileManager.default.createSymbolicLink(at: source, withDestinationURL: secret)
        XCTAssertFalse(TopicCard.isInside(source, graph: root))
    }
}
