import XCTest
@testable import CharoiteApp

/// Подписанный бандл должен оставаться ровно таким, каким его нотаризовали.
///
/// Первый подписанный релиз (0.52.0, 18.08): через четыре секунды после
/// запуска вложенный python начал писать `__pycache__/*.pyc` внутрь
/// `Contents/Resources/python`, печать ресурсов сломалась, `spctl` отвечал
/// «a sealed resource is missing or invalid» — для скачавшего DMG это
/// «повреждено» на втором запуске. Лекарство — каталог байткода вне бандла
/// через PYTHONPYCACHEPREFIX, унаследованный всеми дочерними процессами.
final class BundleSealTests: XCTestCase {

    func testБайткодУходитВКэшПользователяАНеВБандл() throws {
        let base = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-seal-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: base) }

        let path = AppDelegate.keepBundleSealed(cachesBase: base)

        XCTAssertTrue(path.hasPrefix(base.path), "кэш байткода — в переданной базе, не где попало")
        XCTAssertFalse(path.contains(".app/Contents/"), "кэш байткода не может лежать внутри бандла")
        var isDir: ObjCBool = false
        XCTAssertTrue(FileManager.default.fileExists(atPath: path, isDirectory: &isDir) && isDir.boolValue,
                      "каталог создан заранее — python не должен упасть на первом импорте")
        XCTAssertEqual(String(cString: getenv("PYTHONPYCACHEPREFIX")), path,
                       "переменную видят дочерние процессы (демон, обработка встреч)")
    }
}
