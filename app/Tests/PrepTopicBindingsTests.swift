import XCTest
@testable import CharoiteApp

final class PrepTopicBindingsTests: XCTestCase {
    func testPersistedBindingSurvivesTitleChangeAndResetRestoresTitle() {
        var bindings = PrepTopicBindings(data: Data())
        bindings.set("  Платёжный   провайдер \n", key: "series")
        bindings = PrepTopicBindings(data: bindings.data)
        XCTAssertEqual(bindings.topic(key: "series", title: "Еженедельный синк"), "Платёжный провайдер")
        XCTAssertEqual(bindings.topic(key: "other", title: "Другой проект (перенос)"), "Другой проект")
        bindings.set("", key: "series")
        XCTAssertEqual(bindings.topic(key: "series", title: "Новое название"), "Новое название")
    }

    func testGraphCalendarAndSeriesAreSeparateIdentities() {
        let graph = URL(fileURLWithPath: "/tmp/graph")
        let key = PrepTopicBindings.key(graph: graph, calendar: "calendar", item: "series")
        XCTAssertNotEqual(key, PrepTopicBindings.key(graph: graph, calendar: "other", item: "series"))
        XCTAssertNotEqual(key, PrepTopicBindings.key(graph: URL(fileURLWithPath: "/tmp/other"), calendar: "calendar", item: "series"))
        XCTAssertNotEqual(key, PrepTopicBindings.key(graph: graph, calendar: "calendar", item: "other"))
        XCTAssertNil(PrepTopicBindings.key(graph: graph, calendar: "", item: "series"))
    }

    func testCorruptStorageAndUnknownIdentityFallBackToTitle() {
        let bindings = PrepTopicBindings(data: Data([0xff]))
        XCTAssertEqual(bindings.topic(key: nil, title: "Проект (синк)"), "Проект")
    }
}
