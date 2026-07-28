import XCTest
@testable import CharoiteApp

/// Нарезка markdown: то, ради чего она делается, — чтобы хвост длинного файла
/// вообще попал в индекс, а блок из середины оставался узнаваемым.
final class ChunkerTests: XCTestCase {
    func testSectionsSplitByHeadings() {
        let doc = """
        # Встреча 24 июля
        Вступление, все собрались.

        ## Оплата
        Решили брать YuPay: комиссия 2.8%.

        ## Сроки
        Запуск через две недели.
        """
        let chunks = Chunker.chunks(of: doc, title: "2026-07-24 Встреча")
        // Короткий документ остаётся ОДНИМ блоком, и это правильно: дробить
        // заметку на 200 знаков по секциям в 40 знаков — тот самый антипаттерн
        // «слишком мелкие чанки», из-за которого recall падает в разы.
        // Важно другое: структура обязана дойти до эмбеддера.
        XCTAssertEqual(chunks.count, 1, "короткая заметка раздроблена без нужды")
        let forEmbedding = chunks[0].embeddingText
        XCTAssertTrue(forEmbedding.contains("YuPay"))
        XCTAssertTrue(forEmbedding.contains("Оплата") && forEmbedding.contains("Сроки"),
                      "заголовки секций не дошли до эмбеддера: \(forEmbedding)")
    }

    func testBreadcrumbCarriesFileAndHeadingPath() {
        let doc = """
        # Проект
        ## Риски
        ### Бюджет
        Не хватает на GPU.
        """
        let chunk = Chunker.chunks(of: doc, title: "Ядра/Пилот").first
        let crumb = chunk?.breadcrumb ?? ""
        XCTAssertTrue(crumb.contains("Ядра/Пилот"), "нет имени файла: \(crumb)")
        // Заголовки живут либо в крошке (когда секция отдельная), либо в теле
        // блока (когда короткие секции склеены) — эмбеддер видит их в любом
        // случае, а это и есть цель.
        let forEmbedding = chunk?.embeddingText ?? ""
        XCTAssertTrue(forEmbedding.contains("Риски") && forEmbedding.contains("Бюджет"),
                      "путь заголовков потерян: \(forEmbedding)")
    }

    /// Главное: длинный документ не должен терять хвост.
    func testLongSectionsGetOwnBreadcrumbs() {
        let long = { (s: String) in String(repeating: s + " ", count: 120) }
        let doc = """
        # Встреча
        ## Оплата
        \(long("обсуждали комиссию и сроки подключения провайдера"))
        ## Инфраструктура
        \(long("считали мощности кластера и стоимость GPU"))
        """
        let chunks = Chunker.chunks(of: doc, title: "Встреча")
        let payment = chunks.first { $0.text.contains("комиссию") }
        let infra = chunks.first { $0.text.contains("кластера") }
        XCTAssertNotNil(payment)
        XCTAssertNotNil(infra)
        XCTAssertTrue(payment?.breadcrumb.contains("Оплата") == true,
                      "крошка блока: \(payment?.breadcrumb ?? "—")")
        XCTAssertTrue(infra?.breadcrumb.contains("Инфраструктура") == true,
                      "крошка блока: \(infra?.breadcrumb ?? "—")")
    }

    func testTailOfLongDocumentIsChunked() {
        let filler = String(repeating: "Обсуждали регламент и сроки поставки. ", count: 900)
        let doc = "# Встреча\n\n" + filler + "\n\nИТОГ: переносим релиз на сентябрь."
        XCTAssertGreaterThan(doc.count, 30_000)

        let chunks = Chunker.chunks(of: doc, title: "Встреча")

        XCTAssertGreaterThan(chunks.count, 10, "длинный документ не разрезан")
        XCTAssertTrue(chunks.contains { $0.text.contains("переносим релиз на сентябрь") },
                      "хвост документа не попал ни в один блок — ровно то, из-за чего "
                      + "решения в конце встречи были невидимы для семантики")
    }

    func testChunksStayUnderEmbedderLimit() {
        // Ollama режет вход bge-m3 около 12 300 знаков — блок обязан быть заметно короче.
        let filler = String(repeating: "фраза без конца ", count: 5000)
        let chunks = Chunker.chunks(of: "# Раз\n\n" + filler, title: "Файл")
        for c in chunks {
            XCTAssertLessThan(c.embeddingText.count, 4000,
                              "блок \(c.embeddingText.count) знаков — рискует быть обрезанным")
        }
    }

    func testOverlapKeepsBoundaryPhrase() {
        let a = String(repeating: "Первая часть разговора. ", count: 90)
        let b = String(repeating: "Вторая часть разговора. ", count: 90)
        let doc = "# Разговор\n\n" + a + "\n\nГРАНИЧНАЯ_ФРАЗА_НА_СТЫКЕ\n\n" + b
        let chunks = Chunker.chunks(of: doc, title: "Разговор")
        let hits = chunks.filter { $0.text.contains("ГРАНИЧНАЯ_ФРАЗА_НА_СТЫКЕ") }
        XCTAssertFalse(hits.isEmpty, "фраза на стыке потерялась между блоками")
    }

    func testHeadingInsideCodeFenceIsNotHeading() {
        let doc = """
        # Инструкция
        ```bash
        # это комментарий, а не заголовок
        echo привет
        ```
        Конец.
        """
        let chunks = Chunker.chunks(of: doc, title: "Файл")
        XCTAssertFalse(chunks.contains { $0.breadcrumb.contains("это комментарий") },
                       "комментарий из блока кода принят за заголовок")
    }

    func testShortDocumentSurvivesAsSingleChunk() {
        let chunks = Chunker.chunks(of: "Короткая заметка без заголовков.", title: "Заметка")
        XCTAssertEqual(chunks.count, 1)
        XCTAssertTrue(chunks[0].text.contains("Короткая заметка"))
    }

    func testEmptyDocumentGivesNothing() {
        XCTAssertTrue(Chunker.chunks(of: "\n\n   \n", title: "Пусто").isEmpty)
    }
}
