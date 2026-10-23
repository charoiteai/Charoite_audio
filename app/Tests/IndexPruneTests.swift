import XCTest
@testable import CharoiteApp

extension SemanticIndex {
    /// Снимок обхода для тестов: номер — как у поиска, `alive: nil` — без prune.
    func testWalk(_ alive: Set<String>? = nil) -> Walk {
        Walk(seq: beginWalk(), alive: alive)
    }
}

/// Prune индекса стирает запись только у файла, которого нет на диске, и
/// стирает её сразу, даже если идёт доиндексация (№768).
///
/// Раньше снимок prune строился из ПРОЧИТАННЫХ файлов: заметка, которую
/// iCloud выгрузил или которую не открыть, теряла запись и уходила на
/// переэмбеддинг в общую с подсказками Ollama; подпапка без доступа молча
/// выпадала из обхода целиком. А снимок, пришедший во время идущей задачи,
/// пропускался вместе со своим prune — превью забытой встречи жили в индексе
/// до следующего поиска.
final class IndexPruneTests: XCTestCase {
    /// Эмбеддер стоит в воротах на тексте с меткой ЗАТОР, остальное отдаёт сразу.
    private actor Gate {
        private var open = false
        private var waiters: [CheckedContinuation<Void, Never>] = []
        func wait() async {
            if open { return }
            await withCheckedContinuation { waiters.append($0) }
        }
        func release() {
            open = true
            waiters.forEach { $0.resume() }
            waiters = []
        }
    }

    private func gatedIndex(store: URL? = nil, loadStore: Bool = false)
        -> (SemanticIndex, Gate, AsyncStream<Void>) {
        let gate = Gate()
        let entered = AsyncStream<Void>.makeStream()
        let index = makeTestIndex(store: store, loadStore: loadStore) { texts in
            if texts.contains(where: { $0.contains("ЗАТОР") }) {
                entered.continuation.yield(())
                await gate.wait()
            }
            return texts.map { _ in [1, 0] }
        }
        addTeardownBlock { await gate.release() }
        return (index, gate, entered.stream)
    }

    private func search(_ graph: URL, _ index: SemanticIndex) async {
        _ = await ArchiveSearch.localSearchOutcome(query: "бюджет проекта", limit: 3, snippet: 200,
                                                   root: graph, semanticIndex: index)
    }

    private func touch(_ url: URL, ahead seconds: TimeInterval) throws {
        try FileManager.default.setAttributes([.modificationDate: Date().addingTimeInterval(seconds)],
                                              ofItemAtPath: url.path)
    }

    func testUnreadableFileKeepsItsRecordDeletedFileLosesIt() async throws {
        let open = "Ядра/открытый-768.md", broken = "Ядра/битый-768.md", gone = "Ядра/удалённый-768.md"
        let graph = try makeTestGraph([(open, "обсудили бюджет проекта"),
                                       (broken, "бюджет второго проекта"),
                                       (gone, "бюджет третьего проекта")])
        defer { try? FileManager.default.removeItem(at: graph) }
        let index = makeTestIndex { texts in texts.map { _ in [1, 0] } }

        await search(graph, index)
        await index.drainForTests()
        for rel in [open, broken, gone] {
            let chunks = await index.chunkCount(of: rel)
            XCTAssertGreaterThan(chunks, 0, "\(rel) не попал в индекс — тест проверял бы пустоту")
        }

        // не UTF-8 — GraphCache не откроет файл; mtime вперёд, чтобы кэш текстов не отдал старое
        let brokenURL = graph.appendingPathComponent(broken)
        try Data([0xFF, 0xFE, 0x41]).write(to: brokenURL)
        try touch(brokenURL, ahead: 10)
        try FileManager.default.removeItem(at: graph.appendingPathComponent(gone))

        await search(graph, index)
        await index.drainForTests()
        let brokenChunks = await index.chunkCount(of: broken)
        let goneChunks = await index.chunkCount(of: gone)
        XCTAssertGreaterThan(brokenChunks, 0, "файл на диске, который не открылся, потерял запись индекса")
        XCTAssertEqual(goneChunks, 0, "удалённый файл остался в индексе")
    }

    func testLockedSubfolderKeepsItsRecords() async throws {
        let open = "Люди/открытый-768.md", hidden = "Закрытая-768/внутри-768.md"
        let graph = try makeTestGraph([(open, "обсудили бюджет проекта"),
                                       (hidden, "бюджет закрытого проекта")])
        let locked = graph.appendingPathComponent("Закрытая-768")
        defer {
            try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: locked.path)
            try? FileManager.default.removeItem(at: graph)
        }
        let index = makeTestIndex { texts in texts.map { _ in [1, 0] } }

        await search(graph, index)
        await index.drainForTests()
        let before = await index.chunkCount(of: hidden)
        XCTAssertGreaterThan(before, 0, "файл подпапки не попал в индекс — тест проверял бы пустоту")

        try FileManager.default.setAttributes([.posixPermissions: 0o000], ofItemAtPath: locked.path)
        await search(graph, index)
        await index.drainForTests()
        let after = await index.chunkCount(of: hidden)
        XCTAssertGreaterThan(after, 0, "подпапка без доступа выпала из обхода, и prune стёр её файлы")
    }

    /// Ошибка перечисления — путь от корня графа (не путь машины, и через
    /// симлинк /var → /private/var тоже); запертая скрытая папка не в счёт.
    func testWalkErrorsNameRelPathAndSkipHiddenFolders() throws {
        let graph = try makeTestGraph([("Закрытая-768/внутри.md", "x"), (".obsidian/вид.json", "{}")])
        let locked = ["Закрытая-768", ".obsidian"].map { graph.appendingPathComponent($0) }
        defer {
            for url in locked {
                try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: url.path)
            }
            try? FileManager.default.removeItem(at: graph)
        }
        for url in locked {
            try FileManager.default.setAttributes([.posixPermissions: 0o000], ofItemAtPath: url.path)
        }
        let errors = ArchiveSearch.WalkErrors(graph: graph.resolvingSymlinksInPath())
        let walker = try XCTUnwrap(FileManager.default.enumerator(
            at: graph, includingPropertiesForKeys: nil, options: [], errorHandler: errors.record))
        while walker.nextObject() != nil {}
        XCTAssertEqual(errors.list.map(\.rel), ["Закрытая-768"])
        XCTAssertNil(errors.alive(["Люди/a.md"]), "неполный обход отдал ключи для prune")
    }

    func testForgottenMeetingLeavesIndexWhileIndexingRuns() async throws {
        let forgotten = "Встречи/забытая-768.md", node = "Ядра/узел-768.md", slow = "Ядра/затор-768.md"
        let graph = try makeTestGraph([(forgotten, "решили: бюджет проекта режем"),
                                       (node, "узел про бюджет проекта")])
        defer { try? FileManager.default.removeItem(at: graph) }
        let store = tempIndexStore()
        let (index, gate, entered) = gatedIndex(store: store)

        await search(graph, index)
        await index.drainForTests()
        let indexed = await index.chunkCount(of: forgotten)
        XCTAssertGreaterThan(indexed, 0, "встреча не попала в индекс — тест проверял бы пустоту")

        // новый файл держит доиндексацию в воротах
        try "ЗАТОР бюджет проекта".write(to: graph.appendingPathComponent(slow), atomically: true, encoding: .utf8)
        await search(graph, index)
        let inside = await finishesWithin(seconds: 10) { for await _ in entered { break } }
        guard inside else { return XCTFail("доиндексация не дошла до эмбеддера за 10 с") }

        // «Забыть встречу», пока задача ждёт эмбеддер, и следующий поиск
        try FileManager.default.removeItem(at: graph.appendingPathComponent(forgotten))
        await search(graph, index)
        let during = await index.chunkCount(of: forgotten)
        XCTAssertEqual(during, 0, "prune поиска проглочен идущей задачей — превью забытой встречи живёт")

        await gate.release()
        await index.drainForTests()
        let after = await index.chunkCount(of: forgotten)
        let slowChunks = await index.chunkCount(of: slow)
        XCTAssertEqual(after, 0, "задача вернула забытую встречу в индекс")
        XCTAssertGreaterThan(slowChunks, 0, "задача не дописала свой файл")

        // и на диске: файл индекса пишет задача-писатель
        await index.close()
        let reread = makeTestIndex(store: store, loadStore: true) { texts in texts.map { _ in [1, 0] } }
        let onDisk = await reread.chunkCount(of: forgotten)
        XCTAssertEqual(onDisk, 0, "в файле индекса осталось превью забытой встречи")
    }

    /// Файл из снимка идущей задачи удалили, пока она ждала эмбеддер: запись
    /// в конце задачи его не воскрешает.
    func testWriterDoesNotResurrectFileDeletedMeanwhile() async throws {
        let (index, gate, entered) = gatedIndex()
        let started = await index.refreshInBackground(
            files: [(path: "A.md", mtime: 1, text: "# A\n\nЗАТОР текст встречи")],
            walk: index.testWalk(["A.md"]))
        guard started else { return XCTFail("снимок не принят") }
        let inside = await finishesWithin(seconds: 10) { for await _ in entered { break } }
        guard inside else { return XCTFail("эмбеддер не вызван за 10 с") }

        let second = await index.refreshInBackground(files: [], walk: index.testWalk([]))
        XCTAssertFalse(second, "при занятой ячейке второй писатель не ставится")
        await gate.release()
        await index.drainForTests()
        let chunks = await index.chunkCount(of: "A.md")
        XCTAssertEqual(chunks, 0, "задача записала файл, удалённый по более новому обходу")
    }

    /// Снимок старого обхода, пришедший после нового, не прунит.
    func testOlderWalkDoesNotPruneAfterNewer() async throws {
        let index = makeTestIndex { texts in texts.map { _ in [1, 0] } }
        await index.refresh(files: [(path: "B.md", mtime: 1, text: "# B\n\nновый узел")])
        let older = await index.testWalk([])          // обход начался до появления B
        let newer = await index.testWalk(["B.md"])
        await index.refreshInBackground(files: [], walk: newer)
        await index.drainForTests()
        await index.refreshInBackground(files: [], walk: older)
        await index.drainForTests()
        let chunks = await index.chunkCount(of: "B.md")
        XCTAssertGreaterThan(chunks, 0, "медленный старый обход стёр файл, который видел новый")
    }
}
