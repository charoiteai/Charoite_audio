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

    func testPlannedButUnwrittenBufferLeavesItsSlotToTheNextGap() {
        // Арифметика оси: буфер, которому дали `.write`, но не записали
        // (`didWrite` не позван), — его место заливает следующая дыра. Сброс
        // якоря сдвигал бы канал на длину провала (выход 1, Haiku I2). Что
        // файл при сбое откатывается целиком — `testAppendAllRollsBackPartialWrite`.
        var axis = ChannelAxis(rate: rate)
        feed(&axis, from: 100, count: 50, epoch: 100)
        XCTAssertEqual(axis.plan(host: 101, epoch: 100), .write)    // этот буфер не лёг
        XCTAssertEqual(axis.plan(host: 105, epoch: 100), .fill(zeros: 4 * rate))
    }

    func testFileAheadOfAxisRebasesAndCounts() {
        var axis = ChannelAxis(rate: rate)
        feed(&axis, from: 100, count: 50, epoch: 100)
        XCTAssertEqual(axis.plan(host: 100.5, epoch: 100), .write)   // файл впереди на 0,5 с
        XCTAssertEqual(axis.rebases, 1)
        axis.didWrite(buf)
        // отсчёт от этого буфера: следующая дыра заливается целиком
        XCTAssertEqual(axis.plan(host: 100.5 + Double(buf) / Double(rate) + 1, epoch: 100),
                       .fill(zeros: rate))
    }

    func testGarbageTimeBreaksInsteadOfTrapping() {
        var axis = ChannelAxis(rate: 48_000)
        feed(&axis, from: 100, count: 5, epoch: 100)
        guard case .broke = axis.plan(host: 1e300, epoch: 100)
        else { return XCTFail("мусорное время — разрыв, а не Int(...) за пределом") }
    }

    func testFailedAppendNeverShrinksTheFile() throws {
        // Демон читает файл хвостом: обрезка под его позицией увела бы чтение
        // за конец, а перезапуск канала — к началу встречи (Opus C2). Легшая
        // часть остаётся, позиция — в конце, счёт оси — по размеру.
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("axis-\(UUID().uuidString).raw")
        FileManager.default.createFile(atPath: url.path, contents: Data(count: 10))
        defer { try? FileManager.default.removeItem(at: url) }
        let h = try FileHandle(forWritingTo: url)
        defer { try? h.close() }
        try h.seekToEnd()
        struct Disk: Error {}
        // первый кусок лёг, второй упал — как заливка нулей на полном диске
        XCTAssertFalse(h.appendAll { h in
            try h.write(contentsOf: Data(count: 96_000))
            throw Disk()
        })
        XCTAssertEqual(h.endBytes(), 96_010)
        XCTAssertTrue(h.appendAll { try $0.write(contentsOf: Data(count: 4)) })
        try h.synchronize()
        XCTAssertEqual(try Data(contentsOf: url).count, 96_014, "файл только растёт")
        var axis = ChannelAxis(rate: rate)
        axis.resync(fileBytes: 96_014)
        XCTAssertEqual(axis.fileFrames, 48_007)
    }

    func testRestartedStreamCountsEachChannelFromItsOwnFirstBuffer() {
        // Дыра пересоздания общая: системный звук вернулся через 29 с, микрофон —
        // через 31. Решай каждый канал сам, системный залил бы 29 с, а микрофон
        // разорвал бы — каналы разошлись бы на ~29 с. Как до №655 — оба от себя
        // (Opus C1).
        var s = ChannelAxis(rate: rate), m = ChannelAxis(rate: 48_000)
        feed(&s, from: 100, count: 500, epoch: 100)
        for i in 0..<500 {
            XCTAssertEqual(m.plan(host: 100 + Double(i) * 0.02, epoch: 100), .write)
            m.didWrite(960)
        }
        s.streamRestarted(); m.streamRestarted()
        XCTAssertEqual(s.plan(host: 139, epoch: 100), .write)
        XCTAssertEqual(m.plan(host: 141, epoch: 100), .write)
        XCTAssertEqual(s.fileFrames / rate, m.fileFrames / 48_000, "каналы на одной шкале")
        // внутри нового потока дыры снова заливаются
        s.didWrite(buf)
        XCTAssertEqual(s.plan(host: 139 + 0.02 + 1, epoch: 100), .fill(zeros: rate))
    }

    func testFailedFillRebasesInsteadOfGrowingEveryBuffer() {
        // Заливка не легла — дыра живёт как до №655, следующий буфер её не
        // планирует снова, всё длиннее (Opus I1).
        var axis = ChannelAxis(rate: rate)
        feed(&axis, from: 100, count: 50, epoch: 100)
        guard case .fill = axis.plan(host: 102, epoch: 100) else { return XCTFail("дыра 1 с") }
        axis.fillFailed(host: 102, fileBytes: 2 * axis.fileFrames)
        XCTAssertEqual(axis.breaks, 1)
        axis.didWrite(buf)
        XCTAssertEqual(axis.plan(host: 102.02, epoch: 100), .write)
    }

    func testDisabledAxisWritesAsBefore() {
        var axis = ChannelAxis(rate: 48_000)
        feed(&axis, from: 100, count: 5, epoch: 100)
        axis.disable("смена частоты")        // пришёл буфер другой частоты
        XCTAssertEqual(axis.plan(host: 200, epoch: 100), .write)
        XCTAssertEqual(axis.gaps, 0)
    }

    func testChannelEarlierThanEpochRebasesAndKeepsItsSound() {
        // Первый буфер второго канала несёт время раньше эпохи сессии на 0,15 с:
        // отсчёт заново от него, звук не режется, дальнейшие дыры — целиком.
        var axis = ChannelAxis(rate: rate)
        var t = 99.85
        for _ in 0..<50 {
            if case .fill(let z) = axis.plan(host: t, epoch: 100) { axis.didFill(z) }
            axis.didWrite(buf)
            t += Double(buf) / Double(rate)
        }
        XCTAssertEqual(axis.rebases, 1)
        XCTAssertEqual(axis.fileFrames, 50 * buf)
        XCTAssertEqual(axis.plan(host: t + 1, epoch: 100), .fill(zeros: rate))
    }

    func testChannelMinutesBeforeEpochIsNotEmptied() {
        // Время канала на минуту раньше эпохи (чужие часы): каждый буфер — в
        // файл, переякорение одно (выход 2 №655: срез обнулял весь канал).
        var axis = ChannelAxis(rate: rate)
        feed(&axis, from: 40, count: 200, epoch: 100)
        XCTAssertEqual(axis.fileFrames, 200 * buf)
        XCTAssertEqual(axis.rebases, 1)
    }

    func testResyncTakesFileSizeOrDisables() {
        var axis = ChannelAxis(rate: rate)
        feed(&axis, from: 100, count: 10, epoch: 100)
        axis.resync(fileBytes: 2 * 7 * buf)         // сбой записи — счёт по файлу
        XCTAssertEqual(axis.fileFrames, 7 * buf)
        XCTAssertFalse(axis.disabled)
        axis.resync(fileBytes: 2 * 7 * buf + 1)     // полкадра — дальше по оси нельзя
        XCTAssertTrue(axis.disabled)
        XCTAssertEqual(axis.disabledReason, "в файле полкадра")
        XCTAssertEqual(axis.plan(host: 200, epoch: 100), .write)
    }

    func testUnreadableSizeDisablesWithReason() {
        var axis = ChannelAxis(rate: rate)
        axis.resync(fileBytes: nil)
        XCTAssertEqual(axis.disabledReason, "размер файла не прочесть")
    }

    func testFailedWriteLeavesPositionAtRealEnd() throws {
        // Дескриптор только на чтение: запись падает. Позиция — в конце файла,
        // куда пойдёт следующая запись (выход 3, Sonnet I1).
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("axis-ro-\(UUID().uuidString).raw")
        try Data(count: 10).write(to: url)
        defer { try? FileManager.default.removeItem(at: url) }
        let h = try FileHandle(forReadingFrom: url)
        defer { try? h.close() }
        try h.seek(toOffset: 4)
        XCTAssertFalse(h.appendAll { try $0.write(contentsOf: Data(count: 6)) })
        XCTAssertEqual(try h.offset(), 10)
        XCTAssertEqual(h.endBytes(), 10)
    }
}
