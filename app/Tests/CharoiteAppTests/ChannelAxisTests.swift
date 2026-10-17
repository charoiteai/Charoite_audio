import XCTest
@testable import CharoiteApp

/// Ось канала записи (№655): выброшенный буфер больше не сдвигает канал до
/// конца встречи. Замер 15.10 — микрофон ушёл от системного звука ступенями
/// до −5,2 с за 31 минуту. Здесь — арифметика оси на числах, без живого SCK.
final class ChannelAxisTests: XCTestCase {

    private let rate = 16_000
    private let buf = 320          // 20 мс при 16 кГц

    /// Гоним буферы подряд от `t0`, каждый на своём месте.
    private func feed(_ axis: inout ChannelAxis, from t0: Double, count: Int, epoch: Double) {
        for i in 0..<count {
            let plan = axis.plan(host: t0 + Double(i * buf) / Double(rate), epoch: epoch)
            XCTAssertEqual(plan, .write, "буфер \(i) идёт подряд — нулей нет")
            axis.didWrite(buf)
        }
    }

    func testContiguousBuffersNeedNoZeros() {
        var axis = ChannelAxis(rate: rate)
        feed(&axis, from: 100, count: 500, epoch: 100)
        XCTAssertEqual(axis.fileFrames, 500 * buf)
        XCTAssertEqual(axis.gaps, 0)
    }

    func testDroppedSecondIsFilledWithZerosBeforeNextBuffer() {
        var axis = ChannelAxis(rate: rate)
        feed(&axis, from: 100, count: 50, epoch: 100)                // 1 с
        // секунда буферов не дошла — следующий пришёл со своим временем
        let plan = axis.plan(host: 102, epoch: 100)
        XCTAssertEqual(plan, .fill(zeros: rate), "ровно секунда нулей на месте пропавших буферов")
        axis.didFill(rate)
        axis.didWrite(buf)
        // дальше ось снова сходится с файлом
        XCTAssertEqual(axis.plan(host: 102 + Double(buf) / Double(rate), epoch: 100), .write)
        XCTAssertEqual(axis.gaps, 1)
        XCTAssertEqual(axis.zeroFrames, rate)
    }

    func testStepsDoNotAccumulate() {
        // Ступени замера 15.10: семь выпадений по ~1 с. Без оси — −5,2 с к концу.
        var axis = ChannelAxis(rate: rate)
        var t = 100.0
        for _ in 0..<7 {
            for _ in 0..<500 {                                         // 10 с подряд
                if case .fill(let z) = axis.plan(host: t, epoch: 100) { axis.didFill(z) }
                axis.didWrite(buf)
                t += Double(buf) / Double(rate)
            }
            t += 1.0                                                  // выпала секунда
        }
        let want = Int(((t - 100) * Double(rate)).rounded())
        // последний пропуск ещё не залит: файл отстаёт ровно на него, не на семь
        XCTAssertEqual(axis.fileFrames, want - rate, accuracy: 2)
        XCTAssertEqual(axis.gaps, 6)
    }

    func testJitterBelowThresholdIsNotAGap() {
        var axis = ChannelAxis(rate: rate)
        feed(&axis, from: 100, count: 10, epoch: 100)
        // буфер на 50 мс позже своего места — дрожание, не дыра
        XCTAssertEqual(axis.plan(host: 100 + Double(10 * buf) / Double(rate) + 0.05, epoch: 100), .write)
        // и раньше своего места — тоже как есть
        axis.didWrite(buf)
        XCTAssertEqual(axis.plan(host: 100.1, epoch: 100), .write)
    }

    func testLateChannelStartGetsLeadingZerosFromSessionEpoch() {
        // Микрофон отдал первый буфер через 3 с после системного: без эпохи
        // сессии весь микрофон сдвинут на 3 с с первой секунды.
        var mic = ChannelAxis(rate: 48_000)
        XCTAssertEqual(mic.plan(host: 103, epoch: 100), .fill(zeros: 3 * 48_000))
    }

    func testGapAboveCeilingBreaksAndRestartsCount() {
        var axis = ChannelAxis(rate: rate)
        feed(&axis, from: 100, count: 50, epoch: 100)
        let after = 101 + ChannelAxis.fillCeiling + 5
        guard case .broke(let gap) = axis.plan(host: after, epoch: 100)
        else { return XCTFail("дыра больше потолка — разрыв, не нули") }
        XCTAssertEqual(gap, ChannelAxis.fillCeiling + 5, accuracy: 0.01)
        axis.didWrite(buf)
        XCTAssertEqual(axis.breaks, 1)
        XCTAssertEqual(axis.zeroFrames, 0, "живой буфер демона не переполняется нулями")
        // отсчёт заново: следующий буфер подряд за этим — без нулей
        XCTAssertEqual(axis.plan(host: after + Double(buf) / Double(rate), epoch: 100), .write)
        // и следующая обычная дыра заливается целиком (якорь — пара, не одна точка)
        axis.didWrite(buf)
        XCTAssertEqual(axis.plan(host: after + 2 * Double(buf) / Double(rate) + 1, epoch: 100),
                       .fill(zeros: rate))
    }

    func testCeilingIsBelowDaemonLiveBuffer() {
        // `BUF_CAP_S = 60` в src/audio.py: заливка больше половины вытесняла бы речь
        XCTAssertLessThanOrEqual(ChannelAxis.fillCeiling, 30)
    }

    func testWriteFailureRestartsCountInsteadOfGrowingGap() {
        var axis = ChannelAxis(rate: rate)
        feed(&axis, from: 100, count: 50, epoch: 100)
        _ = axis.plan(host: 101, epoch: 100)
        axis.writeFailed()                                            // диск отказал
        // сбои подряд: без сброса каждый следующий буфер просил бы всё больше нулей
        XCTAssertEqual(axis.plan(host: 105, epoch: 100), .write)
        axis.didWrite(buf)
        XCTAssertEqual(axis.plan(host: 105 + Double(buf) / Double(rate), epoch: 100), .write)
    }

    func testDisabledAxisWritesAsBefore() {
        var axis = ChannelAxis(rate: 48_000)
        feed(&axis, from: 100, count: 5, epoch: 100)
        axis.disable()                       // пришёл буфер другой частоты
        XCTAssertEqual(axis.plan(host: 200, epoch: 100), .write)
        XCTAssertEqual(axis.gaps, 0)
    }
}
