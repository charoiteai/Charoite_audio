import XCTest
@testable import CharoiteApp

/// Подмашина остановки: главное — что из «демон не умирает» есть ВЫХОД.
///
/// До выноса это состояние жило в пяти полях сервиса и согласовывалось
/// комментариями. Проверить его можно было только чтением кода, а дефект
/// был ровно такой: приложение переставало опрашивать процесс и оставалось
/// в stopping навсегда — повторный Стоп ничего не возобновлял, а выход из
/// программы оставлял зомби с daemon.lock.
final class ShutdownMachineTests: XCTestCase {

    private func run(_ phase: ShutdownPhase, _ event: ShutdownEvent)
    -> (ShutdownPhase, ShutdownAction) {
        ShutdownMachine.next(phase, on: event)
    }

    func testОстановкаЖивогоДемонаНачинаетсяСЗакрытияЗахвата() {
        let (phase, action) = run(.idle, .stopRequested(daemonAlive: true))
        XCTAssertEqual(phase, .waitingDaemon(waits: 0))
        XCTAssertEqual(action, .closeCapture)
    }

    func testМёртвыйДемонЗакрываетВстречуСразу() {
        let (phase, action) = run(.idle, .stopRequested(daemonAlive: false))
        XCTAssertEqual(phase, .done)
        XCTAssertEqual(action, .finish)
    }

    func testПокаДемонЖивПродолжаемСпрашивать() {
        let (phase, action) = run(.waitingDaemon(waits: 3), .pollTick(daemonAlive: true))
        XCTAssertEqual(phase, .waitingDaemon(waits: 4))
        XCTAssertEqual(action, .pollAgain(after: ShutdownMachine.fastPoll))
    }

    func testПослеПределаОжиданийГоворимЧеловекуЧтоПроцессНеОтпускает() {
        let (phase, action) = run(.waitingDaemon(waits: ShutdownMachine.maxWaits - 1),
                                  .pollTick(daemonAlive: true))
        XCTAssertEqual(phase, .stuck)
        XCTAssertEqual(action, .reportStuck)
    }

    func testЗастрявшийДемонПродолжаетПроверятьсяРедко() {
        // Ровно та дуга, которой не было: без неё состояние не рассасывалось
        // никогда, даже когда демон наконец отпускал ресурсы.
        let (phase, action) = run(.stuck, .pollTick(daemonAlive: true))
        XCTAssertEqual(phase, .stuck)
        XCTAssertEqual(action, .pollAgain(after: ShutdownMachine.slowPoll))
        XCTAssertGreaterThan(ShutdownMachine.slowPoll, ShutdownMachine.fastPoll,
                             "редкий опрос не должен нагружать систему как частый")
    }

    func testЗастрявшийДемонОтпустилИВстречаЗакрывается() {
        let (phase, action) = run(.stuck, .pollTick(daemonAlive: false))
        XCTAssertEqual(phase, .done)
        XCTAssertEqual(action, .finish)
    }

    func testПовторныйСтопПоЗастрявшемуДемонуДобиваетЕго() {
        // Раньше повторный Стоп в stopping просто отменял авто-рестарт и
        // возвращался: человек жал кнопку, и ничего не происходило.
        let (phase, action) = run(.stuck, .stopRequested(daemonAlive: true))
        XCTAssertEqual(phase, .waitingDaemon(waits: 0))
        XCTAssertEqual(action, .forceKill)
    }

    func testПовторныйСтопВоВремяОбычногоОжиданияНичегоНеЛомает() {
        let phase = ShutdownPhase.waitingDaemon(waits: 5)
        let (next, action) = run(phase, .stopRequested(daemonAlive: true))
        XCTAssertEqual(next, phase, "процесс уже добивается по расписанию")
        XCTAssertEqual(action, .nothing)
    }

    func testСообщениеОСмертиДемонаЗакрываетОстановкуИзЛюбойФазы() {
        for phase in [ShutdownPhase.waitingDaemon(waits: 0),
                      .waitingDaemon(waits: 12), .stuck] {
            let (next, action) = run(phase, .daemonExited)
            XCTAssertEqual(next, .done, "\(phase)")
            XCTAssertEqual(action, .finish, "\(phase)")
        }
    }

    func testЗапаснойТаймерЗакрываетЗахватНоНеТрогаетЗастрявшегоДемона() {
        let (phase, action) = run(.waitingDaemon(waits: 2), .killTimeout)
        XCTAssertEqual(phase, .waitingDaemon(waits: 2))
        XCTAssertEqual(action, .closeCapture, "capture закрываем, даже если демон жив")

        let (stuckPhase, stuckAction) = run(.stuck, .killTimeout)
        XCTAssertEqual(stuckPhase, .stuck)
        XCTAssertEqual(stuckAction, .nothing, "капчур уже закрыт, добивать нечего")
    }

    func testЗавершённаяОстановкаНеОживаетОтЗапоздалыхСобытий() {
        for event in [ShutdownEvent.pollTick(daemonAlive: true),
                      .killTimeout, .stopRequested(daemonAlive: true)] {
            let (phase, action) = run(.done, event)
            XCTAssertEqual(phase, .done, "\(event)")
            XCTAssertEqual(action, .nothing, "\(event)")
        }
    }

    func testПолныйПутьЗастревшегоДемонаОтСтопаДоОсвобождения() {
        // Сценарий целиком: Стоп → ожидание → застревание → человек жмёт Стоп
        // ещё раз → добиваем → демон наконец умирает.
        var (phase, _) = run(.idle, .stopRequested(daemonAlive: true))
        for _ in 0..<ShutdownMachine.maxWaits {
            (phase, _) = run(phase, .pollTick(daemonAlive: true))
        }
        XCTAssertEqual(phase, .stuck)

        (phase, _) = run(phase, .stopRequested(daemonAlive: true))
        XCTAssertEqual(phase, .waitingDaemon(waits: 0), "повторный Стоп вернул к добиванию")

        let (final, action) = run(phase, .pollTick(daemonAlive: false))
        XCTAssertEqual(final, .done)
        XCTAssertEqual(action, .finish)
    }
}
