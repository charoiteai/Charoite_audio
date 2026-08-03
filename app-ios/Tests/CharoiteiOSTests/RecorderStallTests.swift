import XCTest
@testable import CharoiteiOS

/// Встреча 03.08: на экране бежали тридцать минут, в файле осталась сорок одна
/// секунда. Часы к тому времени уже брали время у рекордера — врал не таймер,
/// а тишина: `currentTime` просто перестал расти, и сказать об этом было некому.
///
/// Здесь проверяется правило, по которому приложение обязано заговорить.
final class RecorderStallTests: XCTestCase {
    func testGrowingFileIsNeverStalled() {
        // файл вырос — сколько бы времени ни прошло с прошлой проверки
        XCTAssertFalse(Recorder.isStalled(fileSeconds: 12,
                                          lastGrowthSeconds: 10,
                                          sinceLastGrowth: 60))
    }

    func testShortPauseIsNotAlarm() {
        // тик таймера дрожит, длительность округляется — секунда покоя ничего
        // не значит, и пугать человека на встрече из-за неё нельзя
        XCTAssertFalse(Recorder.isStalled(fileSeconds: 41,
                                          lastGrowthSeconds: 41,
                                          sinceLastGrowth: 1))
    }

    func testFrozenDurationRaisesAlarmAfterThreshold() {
        XCTAssertTrue(Recorder.isStalled(fileSeconds: 41,
                                         lastGrowthSeconds: 41,
                                         sinceLastGrowth: Recorder.stallAfter + 0.5),
                      "длительность не растёт дольше порога — это остановка")
    }

    func testDurationGoingBackwardsCountsAsStall() {
        // после перезапуска аудиослужбы currentTime может уехать назад:
        // это тем более не рост, и молчать здесь нельзя
        XCTAssertTrue(Recorder.isStalled(fileSeconds: 5,
                                         lastGrowthSeconds: 41,
                                         sinceLastGrowth: 10))
    }

    func testThresholdStaysHumanSized() {
        // порог живёт в одном месте и должен оставаться в человеческих рамках:
        // меньше секунды — ложные тревоги, больше десяти — теряется смысл
        XCTAssertGreaterThanOrEqual(Recorder.stallAfter, 1)
        XCTAssertLessThanOrEqual(Recorder.stallAfter, 10)
    }
}
