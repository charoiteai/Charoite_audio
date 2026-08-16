import XCTest
@testable import CharoiteApp

/// Данные встреч — только владельцу учётной записи.
///
/// Сырой звук и логи демона создавались с правами по умолчанию (0644),
/// каталоги — 0755: на Mac с несколькими учётками второй пользователь читал
/// чужие переговоры, не запросив ни одного разрешения (аудит 16.08).
/// Обещание «ничего не покидает вашу машину» молчало про границу МЕЖДУ
/// пользователями машины, а для банка или клиники это та же граница.
final class PrivateFilesTests: XCTestCase {

    private func temp() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("charoite-priv-\(UUID().uuidString)")
        return url
    }

    private func mode(_ path: String) throws -> Int {
        let attrs = try FileManager.default.attributesOfItem(atPath: path)
        return (attrs[.posixPermissions] as? NSNumber)?.intValue ?? -1
    }

    func testКаталогДанныхЗакрытОтДругихУчёток() throws {
        let dir = try temp()
        defer { try? FileManager.default.removeItem(at: dir) }

        XCTAssertTrue(FileManager.default.createPrivateDirectory(at: dir))
        XCTAssertEqual(try mode(dir.path), 0o700)
    }

    /// Каталоги, созданные версиями до правки, остались открытыми, а
    /// `createDirectory(attributes:)` для существующего каталога молчит.
    func testСтарыйОткрытыйКаталогЗакрывается() throws {
        let dir = try temp()
        defer { try? FileManager.default.removeItem(at: dir) }
        try FileManager.default.createDirectory(
            at: dir, withIntermediateDirectories: true,
            attributes: [.posixPermissions: NSNumber(value: 0o755)])
        XCTAssertEqual(try mode(dir.path), 0o755)

        FileManager.default.createPrivateDirectory(at: dir)
        XCTAssertEqual(try mode(dir.path), 0o700, "каталог остался читаемым для всех")
    }

    func testФайлЗаписиЗакрытОтДругихУчёток() throws {
        let dir = try temp()
        defer { try? FileManager.default.removeItem(at: dir) }
        FileManager.default.createPrivateDirectory(at: dir)

        let raw = dir.appendingPathComponent("system.raw")
        XCTAssertTrue(FileManager.default.createPrivateFile(atPath: raw.path))
        XCTAssertEqual(try mode(raw.path), 0o600, "сырой звук встречи читаем другими")
    }

    func testЧужойФайлЗакрываетсяЗаднимЧислом() throws {
        let dir = try temp()
        defer { try? FileManager.default.removeItem(at: dir) }
        FileManager.default.createPrivateDirectory(at: dir)

        let old = dir.appendingPathComponent("2026-08-16_1200.md")
        FileManager.default.createFile(
            atPath: old.path, contents: Data("кто что решил".utf8),
            attributes: [.posixPermissions: NSNumber(value: 0o644)])
        XCTAssertEqual(try mode(old.path), 0o644)

        FileManager.default.makePrivate(atPath: old.path)
        XCTAssertEqual(try mode(old.path), 0o600)
    }

}
