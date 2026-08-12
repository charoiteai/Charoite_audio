import XCTest
@testable import CharoiteApp

/// Отложенная остановка захвата стирала файлы следующей встречи.
///
/// Пути к манифесту и потокам статические — общие для всех экземпляров.
/// Захват гасится через 13 секунд после «Стоп»: демон в это время дописывает
/// хвост записи, и снимать устройство раньше нельзя (тот самый обрыв, что
/// 05.08 стоил сорока минут встречи).
///
/// Но если в эти 13 секунд начать следующую встречу — а это штатный сценарий,
/// встречи идут одна за другой, — новый экземпляр уже создаёт манифест и
/// потоки по тем же путям, и отложенный `stop()` прежнего сносит их под ним.
/// Вторая встреча остаётся без системного звука, а на macOS 15+, где микрофон
/// приходит тем же потоком, — без обоих каналов (аудит 0.46.0, P0-5).
///
/// Владение определяется по идентификатору сессии в манифесте.
@MainActor
final class CaptureOwnershipTests: XCTestCase {

    private var tmp: URL!
    private let rootKey = "charoite.root"

    override func setUp() {
        super.setUp()
        tmp = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("charoite-capture-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(
            at: tmp.appendingPathComponent("data"), withIntermediateDirectories: true)
        UserDefaults.standard.set(tmp.path, forKey: rootKey)
    }

    override func tearDown() {
        UserDefaults.standard.removeObject(forKey: rootKey)
        try? FileManager.default.removeItem(at: tmp)
        super.tearDown()
    }

    private func writeManifest(session: String?) throws {
        var manifest: [String: Any] = [
            "engine": "screencapturekit",
            "format": "s16le",
            "system": SystemAudioCapture.systemURL.path,
        ]
        if let session { manifest["session"] = session }
        let data = try JSONSerialization.data(withJSONObject: manifest)
        try data.write(to: SystemAudioCapture.manifestURL)
    }

    func testSessionReadFromManifest() throws {
        try writeManifest(session: "abc-123")
        XCTAssertEqual(SystemAudioCapture.manifestSession(), "abc-123")
    }

    /// Манифест уже перезаписан следующей встречей — значит файлы не наши.
    func testForeignSessionIsDetected() throws {
        try writeManifest(session: "новая-встреча")
        XCTAssertNotEqual(SystemAudioCapture.manifestSession(), "старая-встреча",
                          "манифест принадлежит другому захвату — его файлы трогать нельзя")
    }

    /// Манифеста нет — владение недоказуемо.
    func testMissingManifestGivesNoSession() {
        XCTAssertNil(SystemAudioCapture.manifestSession())
    }

    /// Манифест от версии без поля `session`: удалять тоже нельзя, потому что
    /// доказать принадлежность нечем. Лучше оставить пару файлов на диске, чем
    /// снести источник живой встречи.
    func testLegacyManifestWithoutSessionGivesNil() throws {
        try writeManifest(session: nil)
        XCTAssertNil(SystemAudioCapture.manifestSession())
    }

    /// Сторож проводки: удаление обязано стоять за проверкой владения.
    ///
    /// Логику легко починить и потерять при следующей правке `stop()` —
    /// тогда тесты выше останутся зелёными, а встречи снова начнут терять
    /// системный звук.
    func testStopGuardsOwnershipBeforeDeleting() throws {
        let source = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Sources/CharoiteApp/Services/SystemAudioCapture.swift")
        let text = try String(contentsOf: source, encoding: .utf8)

        guard let stopRange = text.range(of: "func stop() async {"),
              let guardRange = text.range(of: "Self.manifestSession() == sessionID"),
              let removeRange = text.range(of: "removeItem(at: Self.manifestURL)")
        else {
            return XCTFail("stop() потерял проверку владения — файлы новой встречи снова под ударом")
        }
        XCTAssertTrue(stopRange.upperBound < guardRange.lowerBound,
                      "проверка владения должна быть внутри stop()")
        XCTAssertTrue(guardRange.upperBound < removeRange.lowerBound,
                      "удаление стоит РАНЬШЕ проверки владения — это и есть дефект")
    }
}
