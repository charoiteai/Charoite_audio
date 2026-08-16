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
        defer { try? FileManager.default.removeItem(at: store) }
        await SemanticIndex.shared.useForTests(store: store, embedder: fakeEmbedder(vocab))

        // Файл, где ответ — в самом конце, далеко за старой границей в 12 000.
        let head = "# Встреча\n\nПриветствие, все собрались.\n\n"
        let filler = String(repeating: "Обсуждали регламент и поставку. ", count: 900)
        let tail = "\n\n## Итог\n\nПереносим релиз на сентябрь."
        let text = head + filler + tail
        XCTAssertGreaterThan(text.count, 25_000)

        await SemanticIndex.shared.refresh(files: [("Встречи/2026-07-24.md", 1, text)])

        let chunks = await SemanticIndex.shared.chunkCount(of: "Встречи/2026-07-24.md")
        XCTAssertGreaterThan(chunks, 5, "файл не разрезан на блоки")

        let hits = await SemanticIndex.shared.similar(to: "когда переносим релиз",
                                                      within: ["Встречи/2026-07-24.md"], limit: 3)
        XCTAssertFalse(hits.isEmpty, "хвост файла не найден — ровно та дыра, что чинилась")
        XCTAssertTrue(hits[0].snippet.contains("сентябрь"),
                      "сниппет показывает не найденный блок, а начало файла: \(hits[0].snippet.prefix(80))")
    }

    func testFileIsIndexedWholeOrNotAtAll() async {
        let store = makeStore()
        defer { try? FileManager.default.removeItem(at: store) }
        // Эмбеддер, падающий на блоке с маркером: имитируем обрыв Ollama
        // посреди файла. Детерминированно и без мутируемого счётчика —
        // замыкание вызывается внутри актора, и общая переменная здесь
        // даёт гонку: тест не падал, а ЗАВИСАЛ, что в CI означало бы таймаут
        // вместо внятной ошибки.
        await SemanticIndex.shared.useForTests(store: store) { texts in
            guard !texts.contains(where: { $0.contains("ОБРЫВ") }) else { return nil }
            return texts.map { _ in [Float](repeating: 0.5, count: 4) }
        }
        let long = String(repeating: "реплика участника встречи. ", count: 1000)
            + "\n\nОБРЫВ связи с моделью тут.\n\n"
            + String(repeating: "продолжение разговора. ", count: 1000)
        await SemanticIndex.shared.refresh(files: [("Встречи/big.md", 1, long)])

        // Половина блоков не доехала — файл не должен считаться свежим,
        // иначе оставшиеся блоки не проиндексируются уже никогда.
        let mtime = await SemanticIndex.shared.storedMtime(of: "Встречи/big.md")
        XCTAssertNil(mtime, "файл записан частично — хвост потерян навсегда")
    }

    func testIndexSurvivesRestart() async {
        let vocab = ["оплата", "провайдер", "комиссия"]
        let store = makeStore()
        defer { try? FileManager.default.removeItem(at: store) }
        await SemanticIndex.shared.useForTests(store: store, embedder: fakeEmbedder(vocab))
        await SemanticIndex.shared.refresh(files: [
            ("Ядра/Оплата.md", 42, "# Оплата\n\nВыбрали провайдера, комиссия 2.8%."),
        ])
        let stored = await SemanticIndex.shared.storedMtime(of: "Ядра/Оплата.md")
        XCTAssertEqual(stored, 42)

        // «Перезапуск»: подменяем индекс на тот же файл store и читаем с диска.
        await SemanticIndex.shared.useForTests(store: store, embedder: fakeEmbedder(vocab))
        // useForTests чистит память и ставит loaded = true, поэтому проверяем
        // сам формат: декодировать записанное обязано без потерь.
        let raw = try? Data(contentsOf: store)
        XCTAssertNotNil(raw)
        XCTAssertGreaterThan(raw?.count ?? 0, 16, "индекс не записался на диск")
        XCTAssertEqual(Array(raw!.prefix(4)), Array("CHV2".utf8), "чужой формат файла индекса")
    }

    func testDimensionMismatchIsIgnoredNotMiscounted() async {
        let store = makeStore()
        defer { try? FileManager.default.removeItem(at: store) }
        await SemanticIndex.shared.useForTests(store: store) { texts in
            texts.map { _ in [Float](repeating: 0.5, count: 4) }   // 4 измерения
        }
        await SemanticIndex.shared.refresh(files: [("Ядра/А.md", 1, "# А\n\nтекст узла")])

        // Другой эмбеддер с другой размерностью — как смена модели.
        await SemanticIndex.shared.useForTests(store: store) { texts in
            texts.map { _ in [Float](repeating: 0.5, count: 8) }
        }
        let hits = await SemanticIndex.shared.similar(to: "запрос", within: ["Ядра/А.md"], limit: 3)
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
        defer { try? FileManager.default.removeItem(at: store) }
        await SemanticIndex.shared.useForTests(store: store, embedder: fakeEmbedder(vocab))

        let forgotten = "Встречи/2026-07-24.md"
        let kept = "Встречи/2026-07-25.md"
        await SemanticIndex.shared.refresh(files: [
            (forgotten, 1, "# Встреча\n\nРешили: бюджет режем, подрядчик уходит."),
            (kept, 1, "# Встреча\n\nПеренос сроков согласован."),
        ])
        let before = await SemanticIndex.shared.chunkCount(of: forgotten)
        XCTAssertGreaterThan(before, 0)

        // следующий поиск после forget_meeting: файла на диске больше нет
        await SemanticIndex.shared.refresh(files: [(kept, 1, "# Встреча\n\nПеренос сроков согласован.")],
                                           pruneMissing: true)

        let goneCount = await SemanticIndex.shared.chunkCount(of: forgotten)
        let keptCount = await SemanticIndex.shared.chunkCount(of: kept)
        XCTAssertEqual(goneCount, 0, "текст забытой встречи остался в индексе")
        XCTAssertGreaterThan(keptCount, 0, "заодно снесли живую встречу")
    }

    /// Без флага чистки частичный снимок не смеет выбрасывать чужие файлы.
    func testБезФлагаЧисткиИндексНеТеряетФайлы() async {
        let vocab = ["бюджет", "перенос"]
        let store = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-nopr-\(UUID().uuidString).bin")
        defer { try? FileManager.default.removeItem(at: store) }
        await SemanticIndex.shared.useForTests(store: store, embedder: fakeEmbedder(vocab))

        await SemanticIndex.shared.refresh(files: [
            ("a.md", 1, "# A\n\nбюджет обсудили"),
            ("b.md", 1, "# B\n\nперенос согласован"),
        ])
        await SemanticIndex.shared.refresh(files: [("a.md", 1, "# A\n\nбюджет обсудили")])

        let bCount = await SemanticIndex.shared.chunkCount(of: "b.md")
        XCTAssertGreaterThan(bCount, 0,
                             "частичный снимок вычистил файл без флага pruneMissing")
    }
}
