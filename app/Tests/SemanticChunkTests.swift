import XCTest
@testable import CharoiteApp

/// Семантика по блокам, а не по файлу целиком.
///
/// Раньше на файл строился один вектор по первым 12 000 знакам. Замер по
/// рабочему графу: 63% содержимого в индекс не попадало — включая все решения,
/// которые принимают в конце встречи. Эти тесты держат исправление.
final class SemanticChunkTests: XCTestCase {
    /// Подменный эмбеддер: «смысл» — доля общих слов длиннее четырёх букв.
    /// Настоящая Ollama в тестах не нужна и недопустима.
    private func fakeEmbedder(_ vocabulary: [String]) -> ([String]) async -> [[Float]]? {
        { texts in
            texts.map { text in
                let low = text.lowercased()
                return vocabulary.map { low.contains($0) ? Float(1) : Float(0) }
            }
        }
    }

    private func makeStore() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-sem-\(UUID().uuidString).bin")
    }

    func testTailOfLongFileBecomesSearchable() async {
        let vocab = ["сентябрь", "приветствие", "регламент", "релиз"]
        let store = makeStore()
        let index = makeTestIndex(store: store, embedder: fakeEmbedder(vocab))

        // Файл, где ответ — в самом конце, далеко за старой границей в 12 000.
        let head = "# Встреча\n\nПриветствие, все собрались.\n\n"
        let filler = String(repeating: "Обсуждали регламент и поставку. ", count: 900)
        let tail = "\n\n## Итог\n\nПереносим релиз на сентябрь."
        let text = head + filler + tail
        XCTAssertGreaterThan(text.count, 25_000)

        await index.refresh(files: [("Встречи/2026-07-24.md", 1, text)])

        let chunks = await index.chunkCount(of: "Встречи/2026-07-24.md")
        XCTAssertGreaterThan(chunks, 5, "файл не разрезан на блоки")

        let hits = await index.similar(to: "когда переносим релиз",
                                                      within: ["Встречи/2026-07-24.md"], limit: 3)
        XCTAssertFalse(hits.isEmpty, "хвост файла не найден — ровно та дыра, что чинилась")
        XCTAssertTrue(hits[0].snippet.contains("сентябрь"),
                      "сниппет показывает не найденный блок, а начало файла: \(hits[0].snippet.prefix(80))")
    }

    func testFileIsIndexedWholeOrNotAtAll() async {
        let store = makeStore()
        // Эмбеддер, падающий на блоке с маркером: имитируем обрыв Ollama
        // посреди файла. Детерминированно и без мутируемого счётчика —
        // замыкание вызывается внутри актора, и общая переменная здесь
        // даёт гонку: тест не падал, а ЗАВИСАЛ, что в CI означало бы таймаут
        // вместо внятной ошибки.
        let index = makeTestIndex(store: store) { texts in
            guard !texts.contains(where: { $0.contains("ОБРЫВ") }) else { return nil }
            return texts.map { _ in [Float](repeating: 0.5, count: 4) }
        }
        let long = String(repeating: "реплика участника встречи. ", count: 1000)
            + "\n\nОБРЫВ связи с моделью тут.\n\n"
            + String(repeating: "продолжение разговора. ", count: 1000)
        await index.refresh(files: [("Встречи/big.md", 1, long)])

        // Половина блоков не доехала — файл не должен считаться свежим,
        // иначе оставшиеся блоки не проиндексируются уже никогда.
        let mtime = await index.storedMtime(of: "Встречи/big.md")
        XCTAssertNil(mtime, "файл записан частично — хвост потерян навсегда")
    }

    func testIndexSurvivesRestart() async {
        let vocab = ["оплата", "провайдер", "комиссия"]
        let store = makeStore()
        let index = makeTestIndex(store: store, embedder: fakeEmbedder(vocab))
        await index.refresh(files: [
            ("Ядра/Оплата.md", 42, "# Оплата\n\nВыбрали провайдера, комиссия 2.8%."),
        ])
        let stored = await index.storedMtime(of: "Ядра/Оплата.md")
        XCTAssertEqual(stored, 42)

        let raw = try? Data(contentsOf: store)
        XCTAssertGreaterThan(raw?.count ?? 0, 16, "индекс не записался на диск")
        XCTAssertEqual(Array((raw ?? Data()).prefix(4)), Array("CHV2".utf8), "чужой формат файла индекса")

        // «Перезапуск»: прежний писатель закрыт, новый экземпляр читает тот
        // же файл с диска — запись обязана декодироваться без потерь.
        await index.close()
        let restarted = makeTestIndex(store: store, loadStore: true, embedder: fakeEmbedder(vocab))
        let reread = await restarted.storedMtime(of: "Ядра/Оплата.md")
        XCTAssertEqual(reread, 42, "после перезапуска запись не прочиталась с диска")
        let chunks = await restarted.chunkCount(of: "Ядра/Оплата.md")
        XCTAssertGreaterThan(chunks, 0)
    }

    func testDimensionMismatchIsIgnoredNotMiscounted() async {
        let store = makeStore()
        let index = makeTestIndex(store: store) { texts in
            texts.map { _ in [Float](repeating: 0.5, count: 4) }   // 4 измерения
        }
        await index.refresh(files: [("Ядра/А.md", 1, "# А\n\nтекст узла")])

        // Другой эмбеддер с другой размерностью — как смена модели: индекс
        // с диска (4 измерения) читает новый экземпляр с эмбеддером на 8.
        await index.close()
        let changed = makeTestIndex(store: store, loadStore: true) { texts in
            texts.map { _ in [Float](repeating: 0.5, count: 8) }
        }
        let count = await changed.count()
        XCTAssertEqual(count, 1, "индекс с диска не прочитан — тест проверял бы пустоту")
        let hits = await changed.similar(to: "запрос", within: ["Ядра/А.md"], limit: 3)
        XCTAssertTrue(hits.isEmpty,
                      "косинус посчитан по обрезку вектора — тихо заниженные похожести")
    }
}

/// Забытая встреча обязана исчезать и из семантического индекса.
///
/// `forget_meeting` вычищает стенограмму, архив, узлы графа и даже снимки
/// облачных ревизий — а индекс хранит по 700 знаков превью на каждый блок
/// каждого файла и удалять записи не умел вовсе. Текст «забытой» встречи
/// продолжал жить в Application Support (аудит 16.08).
extension SemanticChunkTests {

    func testЗабытаяВстречаУходитИзИндекса() async {
        let vocab = ["бюджет", "перенос", "подрядчик"]
        let store = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-forget-\(UUID().uuidString).bin")
        let index = makeTestIndex(store: store, embedder: fakeEmbedder(vocab))

        let forgotten = "Встречи/2026-07-24.md"
        let kept = "Встречи/2026-07-25.md"
        await index.refresh(files: [
            (forgotten, 1, "# Встреча\n\nРешили: бюджет режем, подрядчик уходит."),
            (kept, 1, "# Встреча\n\nПеренос сроков согласован."),
        ])
        let before = await index.chunkCount(of: forgotten)
        XCTAssertGreaterThan(before, 0)

        // следующий поиск после forget_meeting: файла на диске больше нет
        await index.refresh(files: [(kept, 1, "# Встреча\n\nПеренос сроков согласован.")],
                                           pruneMissing: true)

        let goneCount = await index.chunkCount(of: forgotten)
        let keptCount = await index.chunkCount(of: kept)
        XCTAssertEqual(goneCount, 0, "текст забытой встречи остался в индексе")
        XCTAssertGreaterThan(keptCount, 0, "заодно снесли живую встречу")
    }

    /// Без флага чистки частичный снимок не смеет выбрасывать чужие файлы.
    func testБезФлагаЧисткиИндексНеТеряетФайлы() async {
        let vocab = ["бюджет", "перенос"]
        let store = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-nopr-\(UUID().uuidString).bin")
        let index = makeTestIndex(store: store, embedder: fakeEmbedder(vocab))

        await index.refresh(files: [
            ("a.md", 1, "# A\n\nбюджет обсудили"),
            ("b.md", 1, "# B\n\nперенос согласован"),
        ])
        await index.refresh(files: [("a.md", 1, "# A\n\nбюджет обсудили")])

        let bCount = await index.chunkCount(of: "b.md")
        XCTAssertGreaterThan(bCount, 0,
                             "частичный снимок вычистил файл без флага pruneMissing")
    }
}

/// Владение писателем (№534): фоновая задача — собственность экземпляра.
/// Закрытый экземпляр не пишет, даже если эмбеддер ответил после `close()`:
/// иначе хвост persist воссоздавал удалённую копию индекса с превью встреч.
final class IndexOwnershipTests: XCTestCase {
    func testClosedIndexDoesNotWriteAfterLateEmbedder() async throws {
        actor Seen {
            private(set) var left = false
            func markLeft() { left = true }
        }
        let store = tempIndexStore()
        let entered = AsyncStream<Void>.makeStream()
        let hold = AsyncStream<Void>.makeStream()
        let seen = Seen()
        // эмбеддер висит, пока его не отпустят или не отменят, — потом отвечает
        let index = makeTestIndex(store: store) { texts in
            entered.continuation.yield(())
            for await _ in hold.stream { break }
            await seen.markLeft()
            return texts.map { _ in [1, 0] }
        }
        let started = await index.refreshInBackground(
            files: [(path: "C.md", mtime: 1, text: "# C\n\nтекст узла")], pruneMissing: false)
        guard started else { return XCTFail("первый снимок не принят") }
        let stream = entered.stream
        let inside = await finishesWithin(seconds: 10) { for await _ in stream { break } }
        guard inside else { return XCTFail("эмбеддер не вызван за 10 с") }

        // пока задача идёт, второй снимок пропускается, а не ставит второго писателя
        let second = await index.refreshInBackground(
            files: [(path: "D.md", mtime: 1, text: "# D\n\nдругое")], pruneMissing: true)
        XCTAssertFalse(second)

        await index.close()    // отмена отпускает эмбеддер — он отвечает векторами
        let left = await seen.left
        XCTAssertTrue(left, "close() вернулся, не дождавшись писателя")
        XCTAssertFalse(FileManager.default.fileExists(atPath: store.path),
                       "закрытый экземпляр записал файл индекса после ответа эмбеддера")
        let afterClose = await index.refreshInBackground(
            files: [(path: "C.md", mtime: 1, text: "# C\n\nтекст узла")], pruneMissing: false)
        XCTAssertFalse(afterClose, "закрытый экземпляр принял новую задачу")
    }
}
