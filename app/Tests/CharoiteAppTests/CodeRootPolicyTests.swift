import XCTest
@testable import CharoiteApp

/// Откуда берётся код демона — это вопрос о правах, а не о путях.
///
/// Приложение подписано и держит выданные ему разрешения на микрофон и
/// запись экрана; демон наследует их дочерним процессом. Пока код брался
/// из первой попавшейся папки с `src/daemon.py`, любой процесс без TCC-прав
/// мог подложить `~/Charoite_audio/src/daemon.py` — и Charoite выполнил бы
/// его со своими разрешениями. Отмывание TCC-доступа в чистом виде
/// (аудит 16.08), при том что комментарий у запуска демона обещал
/// «бандл подписан и доступен только на чтение».
final class CodeRootPolicyTests: XCTestCase {

    /// Установка «из коробки»: код в подписанном бандле — и точка.
    func testВложенныйКодНеУступаетЗаписываемойКопии() {
        XCTAssertEqual(
            AppSettings.codeSource(embedded: true, explicitRoot: false,
                                   localCodeExists: true),
            .embedded,
            "подложенный ~/Charoite_audio/src/daemon.py снова исполняется "
          + "с правами подписанного приложения")
    }

    /// Разработчик по-прежнему может гонять свой код — но выбрав путь сам,
    /// в Настройках, где этот выбор видно.
    func testЯвныйВыборЧеловекаУважается() {
        XCTAssertEqual(
            AppSettings.codeSource(embedded: true, explicitRoot: true,
                                   localCodeExists: true),
            .chosenByHuman)
    }

    /// Запуск из клона репозитория: бандла нет, код лежит рядом с данными.
    func testБезБандлаКодРядомСДанными() {
        XCTAssertEqual(
            AppSettings.codeSource(embedded: false, explicitRoot: false,
                                   localCodeExists: true),
            .besideData)
    }

    /// Ни бандла, ни локального кода — падать некуда, но и молча брать
    /// чужое неоткуда: остаётся папка данных, и демон честно не стартует.
    /// (16.08: тест передавал explicitRoot: true вопреки собственному
    /// описанию — сценарий по умолчанию оставался непокрытым.)
    func testНичегоНетОстаётсяПапкаДанных() {
        XCTAssertEqual(
            AppSettings.codeSource(embedded: false, explicitRoot: false,
                                   localCodeExists: false),
            .besideData)
    }

    /// Явная папка данных — не разрешение исполнять её код: без тумблера
    /// `charoite.codeFromRoot` код остаётся из бандла, даже если рядом с
    /// данными лежит src/daemon.py. Иначе предложение «взять клон как папку
    /// данных» тянуло бы за собой запуск чужого кода с правами приложения.
    func testПапкаДанныхБезРазрешенияНаКодОстаётсяБандл() {
        XCTAssertEqual(
            AppSettings.codeSource(embedded: true, explicitRoot: true,
                                   localCodeExists: true, codeFromRoot: false),
            .embedded)
    }

    /// Тумблер включён — разработческий режим, как и раньше.
    func testТумблерКодаВключаетКодИзПапки() {
        XCTAssertEqual(
            AppSettings.codeSource(embedded: true, explicitRoot: true,
                                   localCodeExists: true, codeFromRoot: true),
            .chosenByHuman)
    }

    /// Без бандла тумблер не имеет смысла: код и так рядом с данными.
    func testТумблерНеМешаетЗапускуИзКлона() {
        XCTAssertEqual(
            AppSettings.codeSource(embedded: false, explicitRoot: false,
                                   localCodeExists: true, codeFromRoot: false),
            .besideData)
    }

    /// Явный выбор без кода по этому пути не должен уводить от бандла.
    func testПустойЯвныйПутьНеЛишаетБандла() {
        XCTAssertEqual(
            AppSettings.codeSource(embedded: true, explicitRoot: true,
                                   localCodeExists: false),
            .embedded)
    }

    /// Апгрейд со старого идентификатора бандла переносит `charoite.root`,
    /// а ключа `codeFromRoot` в старом домене нет — и его отсутствие
    /// читалось как «можно»: код демона снова шёл из записываемого клона с
    /// правами приложения (аудит DeepSeek 16.08). Миграция обязана
    /// перенести папку как данные и явно закрыть код.
    func testМиграцияПереноситПапкуКакДанныеБезПраваНаКод() throws {
        let suite = "ai.charoite.tests.migration.\(UUID().uuidString)"
        let d = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { d.removePersistentDomain(forName: suite) }

        let moved = AppDelegate.migrateSettings(
            from: ["charoite.root": "~/Charoite_audio", "sufler.language": "ru",
                   "NSWindow Frame main": "0 0 100 100"], into: d)

        XCTAssertEqual(moved, 2, "переносятся только ключи приложения")
        XCTAssertEqual(d.string(forKey: "charoite.root"), "~/Charoite_audio")
        XCTAssertNotNil(d.object(forKey: "charoite.codeFromRoot"),
                        "тумблер кода обязан быть записан явно, а не читаться из умолчания")
        XCTAssertFalse(d.bool(forKey: "charoite.codeFromRoot"),
                       "перенесённая папка — данные; код из неё — только по явному тумблеру")
    }

    /// Пустой или чужой домен — переносить нечего, ничего не пишем.
    func testМиграцияЧужогоДоменаНичегоНеПишет() throws {
        let suite = "ai.charoite.tests.migration.\(UUID().uuidString)"
        let d = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { d.removePersistentDomain(forName: suite) }
        XCTAssertEqual(AppDelegate.migrateSettings(from: ["foo": 1], into: d), 0)
        XCTAssertNil(d.object(forKey: "charoite.codeFromRoot"))
    }
}
