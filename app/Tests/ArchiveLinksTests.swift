import XCTest
@testable import CharoiteApp

final class ArchiveLinksTests: XCTestCase {
    func testPersonFindsMeetingDecisionWithoutQueryWordsAndDoesNotFollowSecondHop() {
        let documents = ["People/Ivan.md": "[[Meetings/One|discussion]]",
                         "Meetings/One.md": "Approved B. [[Systems/Secret]]",
                         "Systems/Secret.md": "Second hop"]
        XCTAssertEqual(ArchiveLinks.neighbors(seeds: ["People/Ivan.md"], documents: documents),
                       [.init(rel: "Meetings/One.md", parent: "People/Ivan.md")])
    }

    func testAmbiguousNamesMissingLinksServicePathsAndTraversalAreNotCandidates() {
        let documents = ["People/Ivan.md": "[[CRM]] [[Missing]] [[../Secret]] [[Cores/_index]]",
                         "People/CRM.md": "person", "Systems/CRM.md": "system", "Cores/_index.md": "service"]
        XCTAssertTrue(ArchiveLinks.neighbors(seeds: ["People/Ivan.md"], documents: documents).isEmpty)
    }

    func testQualifiedNamesAnchorsCyclesAndCandidateLimit() {
        let documents = ["Cores/Topic.md": "[[Meetings/A#Decision]] [[Meetings/B]] [[Cores/Topic]] [[Meetings/A]]",
                         "Meetings/A.md": "A", "Meetings/B.md": "B"]
        XCTAssertEqual(ArchiveLinks.neighbors(seeds: ["Cores/Topic.md"], documents: documents, limit: 1),
                       [.init(rel: "Meetings/A.md", parent: "Cores/Topic.md")])
        XCTAssertTrue(ArchiveLinks.neighbors(seeds: ["Cores/Topic.md"], documents: documents, limit: 0).isEmpty)
    }

    func testArchiveSearchIncludesLinkedSourceInPackedOutcome() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        for folder in ["People", "Meetings"] {
            try FileManager.default.createDirectory(at: root.appendingPathComponent(folder), withIntermediateDirectories: true)
        }
        try "# Иван\n## Встречи\n[[Meetings/One]]".write(to: root.appendingPathComponent("People/Иван.md"), atomically: true, encoding: .utf8)
        try "# Обсуждение\nВыбрали провайдера Б для запуска.".write(to: root.appendingPathComponent("Meetings/One.md"), atomically: true, encoding: .utf8)
        let outcome = await ArchiveSearch.localSearchOutcome(query: "Иван", limit: 2, snippet: 400, root: root)
        XCTAssertTrue(outcome.text.contains("Выбрали провайдера Б"))
        XCTAssertTrue(outcome.rels?.contains("Meetings/One.md") == true)
    }
}
