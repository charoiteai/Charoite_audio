import XCTest
@testable import CharoiteApp

/// Полнота поиска: стеммер, который разводит формы одного слова, теряет
/// совпадения молча — файл со словом «meetings» не находится по «meeting».
final class StemConsistencyTests: XCTestCase {
    func testSingularAndPluralStemTheSame() {
        // Однопроходная таблица ["ing","ed","es","s"] режет ровно один
        // суффикс: meetings→meeting, но meeting→meet. Пара «ед/мн число»
        // одного слова получает РАЗНЫЕ стемы, и совпадение между ними
        // не засчитывается никогда.
        XCTAssertEqual(ArchiveSearch.stem("meetings"), ArchiveSearch.stem("meeting"),
                       "meeting и meetings обязаны сходиться к одному стему")
        XCTAssertEqual(ArchiveSearch.stem("settings"), ArchiveSearch.stem("setting"))
        XCTAssertEqual(ArchiveSearch.stem("recordings"), ArchiveSearch.stem("recording"))
        XCTAssertEqual(ArchiveSearch.stem("findings"), ArchiveSearch.stem("finding"))
    }

    func testExistingStemsStayPut() {
        // Якоря: прежние стемы не должны поехать от любого фикса выше.
        XCTAssertEqual(ArchiveSearch.stem("decided"), "decid")
        XCTAssertEqual(ArchiveSearch.stem("blockers"), "blocker")
        XCTAssertEqual(ArchiveSearch.stem("launching"), "launch")
        // русская ветка не задета
        XCTAssertEqual(ArchiveSearch.stem("встречами"), "встреч")
        XCTAssertEqual(ArchiveSearch.stem("мост"), "мост")
    }
}

// MARK: - Общая машинерия семантических тестов

/// Временный граф из (rel, текст)-пар; удалять за собой.
func makeTestGraph(_ files: [(String, String)]) throws -> URL {
    let dir = FileManager.default.temporaryDirectory
        .appendingPathComponent("charoite-graph-\(UUID().uuidString)")
    for (rel, text) in files {
        let url = dir.appendingPathComponent(rel)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try text.write(to: url, atomically: true, encoding: .utf8)
    }
    return dir
}

func tempIndexStore() -> URL {
    FileManager.default.temporaryDirectory
        .appendingPathComponent("charoite-index-\(UUID().uuidString).json")
}

/// Семантика — не пересортировка лексической выдачи, а второй вход в граф.
///
/// Словарный разрыв — ровно тот случай, ради которого семантический слой
/// существует: вопрос задан другими словами, ни одно слово запроса в файле
/// не встречается. Если кандидаты семантики ограничены файлами, которые уже
/// нашла лексика, такой вопрос не находит ничего никогда — слой оплачен
/// (Ollama, bge-m3, индекс на диске), а его главную работу делать некому.
final class SemanticUnionTests: XCTestCase {
    func testSemanticFindsWhatLexiconMissed() async throws {
        let graph = try makeTestGraph([
            ("Ядра/Бюджет.md", "курс валют и деньги на квартал"),
            ("Ядра/Погода.md", "дожди весь месяц"),
        ])
        defer { try? FileManager.default.removeItem(at: graph) }
        // Эмбеддер-подделка: запрос и файл про деньги — один вектор,
        // остальное — ортогональ. Настоящая Ollama не трогается.
        await SemanticIndex.shared.useForTests(store: tempIndexStore()) { texts in
            texts.map { $0.contains("деньги") || $0.contains("финанс") ? [1, 0] : [0, 1] }
        }
        await SemanticIndex.shared.refresh(files: [
            (path: "Ядра/Бюджет.md", mtime: 1, text: "курс валют и деньги на квартал"),
            (path: "Ядра/Погода.md", mtime: 1, text: "дожди весь месяц"),
        ])

        // Ни «финансовый», ни «прогноз» не встречаются ни в одном файле —
        // лексика пуста, ответ обязан прийти из семантики.
        let out = await ArchiveSearch.localSearch(query: "финансовый прогноз",
                                                  limit: 3, snippet: 200, root: graph)
        XCTAssertTrue(out.contains("Бюджет"),
                      "семантика не смогла добавить файл, которого нет в лексической выдаче; получили: «\(out.prefix(120))»")
        XCTAssertFalse(out.contains("Погода"), "ортогональный файл пролез в выдачу")
    }
}
