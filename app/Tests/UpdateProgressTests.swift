import XCTest
@testable import CharoiteApp

/// «Скачиваю… 0%» не должно означать «загрузка встала».
///
/// 13.08 обновление на 91 МБ шло через прокси около получаса, и всё это время
/// на экране висел ноль: асинхронный `download(from:)` ведёт задачу сам и
/// делегата сессии о ходе загрузки не спрашивает, поэтому проценты не считал
/// никто. Делегат теперь передаётся в сам вызов, а расчёт вынесен в чистую
/// функцию — её и держат эти тесты.
final class UpdateProgressTests: XCTestCase {
    func testPercentGrowsWithBytes() {
        XCTAssertEqual(UpdateService.percent(written: 0, total: 100), 0)
        XCTAssertEqual(UpdateService.percent(written: 50, total: 100), 50)
        XCTAssertEqual(UpdateService.percent(written: 100, total: 100), 100)
    }

    func testRealFileSizeIsCountedHonestly() {
        // Размер архива выпуска 0.49.0. Округление вниз намеренное: показать
        // 25% на 24,99% — значит однажды показать 100% на недокачанном файле.
        let total: Int64 = 91_519_625
        XCTAssertEqual(UpdateService.percent(written: total / 4, total: total), 24)
        XCTAssertEqual(UpdateService.percent(written: total / 2, total: total), 49)
        XCTAssertEqual(UpdateService.percent(written: total - 1, total: total), 99)
        XCTAssertEqual(UpdateService.percent(written: total, total: total), 100)
    }

    func testUnknownSizeShowsNothingInsteadOfZero() {
        // Сервер вправе не прислать длину; ноль на экране читается как
        // «встало», поэтому в таком случае не показываем ничего.
        XCTAssertNil(UpdateService.percent(written: 1_000, total: -1))
        XCTAssertNil(UpdateService.percent(written: 1_000, total: 0))
    }

    func testPercentNeverExceedsHundred() {
        // Догрузка после переподключения может дать байтов больше заявленного.
        XCTAssertEqual(UpdateService.percent(written: 120, total: 100), 100)
    }

    func testNegativeWrittenIsNotShown() {
        XCTAssertNil(UpdateService.percent(written: -5, total: 100))
    }
}
