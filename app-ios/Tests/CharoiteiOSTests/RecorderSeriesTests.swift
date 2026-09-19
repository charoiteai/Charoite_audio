import AVFoundation
import XCTest
@testable import CharoiteiOS

/// №200: серия и манифест на живом рекордере симулятора. Ротацию здесь не
/// вызвать (входа никто не отнимает), поэтому проверяется контракт вокруг
/// неё: стем серии рождается на старте, едет в манифест и гасится
/// терминальным стопом — а не концом ротации (Minor GLM выходного круга).
@MainActor
final class RecorderSeriesTests: XCTestCase {
    private func wait(_ seconds: TimeInterval) {
        let e = expectation(description: "пауза")
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { e.fulfill() }
        waitForExpectations(timeout: seconds + 5)
    }

    func testСерияЖивётОтСтартаДоТерминальногоСтопаИЕдетВМанифест() throws {
        let rec = Recorder()
        XCTAssertNil(rec.seriesStem, "до старта серии нет")
        rec.start(kind: .meeting)
        wait(1.5)
        guard rec.isRecording else {
            throw XCTSkip("симулятор не дал записи — окружению нельзя верить")
        }
        guard let stem = rec.seriesStem else {
            return XCTFail("стем серии не поставлен на старте")
        }
        rec.stop(reason: .encodeError)
        wait(1.5)
        XCTAssertFalse(rec.isRecording)
        XCTAssertNil(rec.seriesStem, "терминальный стоп закрывает серию")
        XCTAssertEqual(rec.lastStopReason, Recorder.StopReason.encodeError.text(terminal: true))
        XCTAssertTrue(rec.lastStopReason?.contains("\(Recorder.maxEncodeErrors)") == true, rec.lastStopReason ?? "nil")
        // манифест уехал парой с аудио: где бы файл ни лежал (outbox/sent), причина рядом
        let audio = try XCTUnwrap(Inbox.lastRecording, "запись должна остаться доступной для «Поделиться»")
        let record = try XCTUnwrap(Recorder.StopRecord.read(nextTo: audio), "манифест едет с аудио")
        XCTAssertEqual(record.kind, .stop)
        XCTAssertEqual(record.reason, .encodeError)
        XCTAssertEqual(record.series, stem)
        XCTAssertEqual(audio.deletingPathExtension().lastPathComponent, stem, "первый файл серии — её стем")
        XCTAssertNotNil(record.seconds)
    }
}
