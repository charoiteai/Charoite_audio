import XCTest
@testable import CharoiteApp

#if os(macOS)
/// Инвентарь «Что знает память»: счёт по контракту папок и служебные файлы.
final class GraphInventoryTests: XCTestCase {

    private func makeGraph(_ build: (URL) throws -> Void) throws -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("graph-inv-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try build(dir)
        addTeardownBlock { try? FileManager.default.removeItem(at: dir) }
        return dir
    }

    private func put(_ root: URL, _ rel: String, _ text: String = "тело") throws {
        let url = root.appendingPathComponent(rel)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try text.write(to: url, atomically: true, encoding: .utf8)
    }

    func testScanCountsContractFoldersAndSkipsServiceFiles() throws {
        let graph = try makeGraph { root in
            try self.put(root, "Встречи/2026-08-21_1202.md")
            try self.put(root, "Meetings/2026-08-22_0900.md")
            // Служебный маркер исключений — НЕ встреча (круг-2, DS+GLM:
            // «Встречи-архив/_исключено.md» завышал счёт на единицу).
            try self.put(root, "Встречи-архив/_исключено.md")
            try self.put(root, "Люди/Мария Соколова.md")
            try self.put(root, "People/Anna.md")
            try self.put(root, "Досье/Подпись.md")
            try self.put(root, "Cores/Release.md", "---\ntype: core\n---\n# Release\nподпись вне CI решена")
        }
        let snap = GraphInventoryService.scan(graph: graph)
        XCTAssertEqual(snap.meetings, 2)
        XCTAssertEqual(snap.nodes, 3)      // Люди + People + ядро Cores
        XCTAssertEqual(snap.dossiers, 1)
        XCTAssertEqual(snap.cores.count, 1)
        // Клик по ядру обязан знать СВОЮ папку — «Ядра/» хардкодом открывал
        // несуществующий путь на en/zh-графе (круг-2, DS+GLM).
        XCTAssertEqual(snap.cores.first?.folder, "Cores")
        XCTAssertEqual(snap.cores.first?.status, "подпись вне CI решена")
    }

    /// Встреча — ключ, а не файл (№757): архив лежит папкой с минутками
    /// внутри, а заметка той же встречи — файлом в «Встречи/». Плоский счёт .md
    /// давал 1 на три встречи этого графа.
    func testMeetingsCountArchiveFoldersOncePerMeeting() throws {
        let graph = try makeGraph { root in
            try self.put(root, "Встречи/2026-07-21_1000.md")
            try self.put(root, "Встречи-архив/2026-07-21 10-00 — Планирование/Минутки.md")
            try self.put(root, "Встречи-архив/2026-07-20 09-00 — Ретро/Минутки.md")
            try self.put(root, "Встречи/2026-07-10_прим_постановка.md")   // без ключа — своя встреча
            try self.put(root, "Встречи-архив/_ОГЛАВЛЕНИЕ.md")             // служебный
            try self.put(root, "Встречи/черновик.txt")                     // не заметка
        }
        XCTAssertEqual(GraphInventoryService.scan(graph: graph).meetings, 3)
    }

    func testMeetingsLabelNamesTheGraph() {
        guard L.lang == "ru" else { return }
        XCTAssertEqual(MemoryScreenPolicy.meetingsLabel(1), "1 встреча в графе")
        XCTAssertEqual(MemoryScreenPolicy.meetingsLabel(2), "2 встречи в графе")
        XCTAssertEqual(MemoryScreenPolicy.meetingsLabel(11), "11 встреч в графе")
    }

    func testCoreStatusIgnoresBodyRuleLines() throws {
        let graph = try makeGraph { root in
            try self.put(root, "Ядра/Тема.md", "# Тема\n\n---\n\nстатус после линейки")
        }
        let snap = GraphInventoryService.scan(graph: graph)
        // «---» в теле — линейка, не фронтматтер: статус не должен пустеть
        // (круг-1, GLM).
        XCTAssertEqual(snap.cores.first?.status, "статус после линейки")
    }
}
#endif
