import XCTest
@testable import CharoiteApp

/// Разовое построение семантического индекса на настоящем графе.
///
/// Не тест, а инструмент: приложение строит индекс фоном по 48 блоков за
/// поиск, и на 4785 блоках это сотня запросов. Здесь то же самое делается за
/// один заход, настоящей Ollama и в настоящий файл индекса.
///
///   CHAROITE_BUILD_INDEX=1 CHAROITE_GRAPH_DIR=~/путь swift test \
///     --package-path app --filter CharoiteAppLiveProbes.BuildRealIndex
final class BuildRealIndex: XCTestCase {
    func testBuild() async throws {
        guard ProcessInfo.processInfo.environment["CHAROITE_BUILD_INDEX"] == "1",
              let raw = ProcessInfo.processInfo.environment["CHAROITE_GRAPH_DIR"],
              !raw.isEmpty else { throw XCTSkip("не запрошено") }
        // пишет в живой индекс приложения — путь разбираем так же строго, как пробы
        let graph = try ProbeTurn.graphDir(raw)

        // Собираем файлы так же, как это делает поиск: с дедупом по содержимому.
        var files: [(path: String, mtime: Double, text: String)] = []
        var seen = Set<Int>()
        let urls = (FileManager.default.enumerator(at: graph,
                        includingPropertiesForKeys: [.contentModificationDateKey],
                        options: [])?.compactMap { $0 as? URL } ?? [])
            .filter { $0.pathExtension == "md" }
            // как ArchiveSearch: служебное — точка в начале компонента; флаг
            // UF_HIDDEN (папки iCloud) приложение не пропускает, и проба тоже (out10)
            .filter { url in !url.pathComponents.contains { $0.hasPrefix(".") && $0.count > 1 } }
            .sorted { a, b in
                let aArch = a.path.contains("/Встречи-архив/")
                let bArch = b.path.contains("/Встречи-архив/")
                return aArch == bArch ? a.path < b.path : !aArch
            }
        for url in urls {
            guard let text = try? String(contentsOf: url, encoding: .utf8),
                  seen.insert(text.hashValue).inserted else { continue }
            let canon = url.resolvingSymlinksInPath().path
            let rel = canon.hasPrefix(graph.path + "/")
                ? String(canon.dropFirst(graph.path.count + 1)) : url.lastPathComponent
            let mtime = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?
                .contentModificationDate?.timeIntervalSince1970 ?? 0
            files.append((rel, mtime, text))
        }
        print("INDEX: файлов к индексации \(files.count)")
        // ключ вне корня — имя файла: два одноимённых схлопнулись бы в одну запись
        let dup = Dictionary(grouping: files, by: \.path).filter { $0.value.count > 1 }.keys.sorted()
        guard dup.isEmpty else { XCTFail("одинаковые ключи индекса: \(dup.prefix(5))"); return }
        // пустой обход — не тот граф: count() ниже прочёл бы прежний индекс и проба позеленела бы
        guard !files.isEmpty else { XCTFail("в графе нет ни одного .md: \(graph.path)"); return }

        // живой индекс под XCTest — только явной подменой (№740)
        _ = FileManager.default.createPrivateDirectory(at: SemanticIndex.liveStoreURL.deletingLastPathComponent())
        await SemanticIndex.shared.useForTests(store: SemanticIndex.liveStoreURL, loadStore: true, embedder: nil)
        addTeardownBlock { await SemanticIndex.shared.endTestSession() }
        // refresh берёт за вызов не больше потолка блоков фонового поиска (48):
        // гоняем, пока очередь убывает; файлы без блоков в индекс не попадают никогда.
        let wanted = files.filter { !Chunker.chunks(of: $0.text, title: ($0.path as NSString).deletingPathExtension).isEmpty }
        guard !wanted.isEmpty else { XCTFail("ни в одном файле графа нет блоков: \(graph.path)"); return }
        func pending() async -> Int {
            var n = 0
            for f in wanted where await SemanticIndex.shared.storedMtime(of: f.path) != f.mtime { n += 1 }
            return n
        }
        let t0 = Date()
        var left = await pending()
        while left > 0 {
            await SemanticIndex.shared.refresh(files: files)
            let now = await pending()
            guard now < left else { break }   // Ollama не отвечает — дальше не сдвинемся
            left = now
        }
        let dt = Date().timeIntervalSince(t0)
        let indexed = await SemanticIndex.shared.count()
        let chunks = await SemanticIndex.shared.totalChunks()
        print("INDEX: готово за \(Int(dt)) с — файлов \(indexed), блоков \(chunks), не проиндексировано \(left)")
        XCTAssertEqual(left, 0, "не проиндексировано \(left) из \(wanted.count) — Ollama недоступна или нет модели bge-m3")
        // память — не диск: persist глотает ошибку записи, а открытое приложение
        // переписывает файл своим снимком — проба сверяет каждый файл графа с диском.
        // Не доиндексировали — причина выше, второе сообщение увело бы от неё.
        if left == 0 {
            let onDisk = SemanticIndex.storedMtimes(at: SemanticIndex.liveStoreURL) ?? [:]
            let absent = wanted.filter { onDisk[$0.path] != $0.mtime }.count
            XCTAssertEqual(absent, 0, "на диске нет \(absent) из \(wanted.count) файлов: \(SemanticIndex.liveStoreURL.path) — не записан или переписан приложением")
        }
    }
}
