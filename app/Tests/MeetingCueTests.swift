import XCTest
@testable import CharoiteApp

/// Запись начиналась только вручную — забыл нажать, встреча потеряна целиком.
///
/// Никакая последующая функция этого не лечит: нет звука — нет ни стенограммы,
/// ни минуток, ни узлов графа. При этом продукт УЖЕ знает, что встреча идёт:
/// календарь читается (opt-in, только чтение) ради брифа по архиву.
///
/// Чего делать нельзя: включать запись самостоятельно. Запись разговора без
/// ведома человека за машиной — ровно то, за что судятся с облачными
/// сервисами, и Чароит с этим не играет. Значит сигнал, а не действие:
/// «встреча началась — начать запись?» с одной кнопкой.
///
/// Этот файл держит логику решения «предлагать или молчать». Она чистая,
/// поэтому проверяется без календаря, микрофона и интерфейса.
final class MeetingCueTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)   // фиксируем «сейчас»

    private func event(_ title: String, startsIn minutes: Double,
                       lasts: Double = 60, attendees: Int = 3,
                       allDay: Bool = false) -> MeetingCue.Event {
        MeetingCue.Event(id: title,
                         title: title,
                         start: now.addingTimeInterval(minutes * 60),
                         end: now.addingTimeInterval((minutes + lasts) * 60),
                         attendees: attendees,
                         isAllDay: allDay)
    }

    func testSuggestsWhenTheMeetingHasJustStarted() {
        let cue = MeetingCue.decide(now: now, events: [event("Планёрка", startsIn: -3)],
                                    isRecording: false, silencedIds: [])
        XCTAssertEqual(cue?.title, "Планёрка")
        XCTAssertEqual(cue?.id, "Планёрка")
    }

    func testSuggestsShortlyBeforeTheStart() {
        let cue = MeetingCue.decide(now: now, events: [event("Ретро", startsIn: 1)],
                                    isRecording: false, silencedIds: [])
        XCTAssertNotNil(cue, "за минуту до начала предложить уже поздно не будет")
    }

    func testStaysQuietLongBeforeAndLongAfter() {
        for offset in [-45.0, -20.0, 15.0, 60.0] {
            let cue = MeetingCue.decide(now: now, events: [event("Созвон", startsIn: offset)],
                                        isRecording: false, silencedIds: [])
            XCTAssertNil(cue, "предложение вне окна начала (смещение \(offset) мин)")
        }
    }

    func testNeverInterruptsAnOngoingRecording() {
        let cue = MeetingCue.decide(now: now, events: [event("Планёрка", startsIn: -2)],
                                    isRecording: true, silencedIds: [])
        XCTAssertNil(cue, "запись уже идёт — предлагать нечего")
    }

    func testDeclinedMeetingIsNotOfferedAgain() {
        let ev = event("Планёрка", startsIn: -2)
        let cue = MeetingCue.decide(now: now, events: [ev],
                                    isRecording: false, silencedIds: [ev.id])
        XCTAssertNil(cue, "«не сейчас» должно означать «не сейчас», а не «спроси снова»")
    }

    func testAllDayEventsAreNotMeetings() {
        let cue = MeetingCue.decide(now: now,
                                    events: [event("Отпуск", startsIn: -2, allDay: true)],
                                    isRecording: false, silencedIds: [])
        XCTAssertNil(cue)
    }

    func testEventWithoutOtherPeopleIsNotAMeeting() {
        // «Забрать посылку» в календаре — не встреча: записывать нечего.
        let cue = MeetingCue.decide(now: now,
                                    events: [event("Забрать посылку", startsIn: -2, attendees: 0)],
                                    isRecording: false, silencedIds: [])
        XCTAssertNil(cue)
    }

    func testTheMeetingThatStartedMostRecentlyWins() {
        let cue = MeetingCue.decide(
            now: now,
            events: [event("Ранняя", startsIn: -9), event("Только что", startsIn: -1)],
            isRecording: false, silencedIds: [])
        XCTAssertEqual(cue?.title, "Только что",
                       "две встречи в окне — предлагаем ту, что началась ближе к «сейчас»")
    }

    func testCueTextNamesTheMeetingAndAsksRatherThanTells() {
        let cue = MeetingCue.decide(now: now, events: [event("Планёрка", startsIn: -2)],
                                    isRecording: false, silencedIds: [])
        let text = cue?.prompt ?? ""
        XCTAssertTrue(text.contains("Планёрка"), "в подсказке нет названия встречи: \(text)")
        XCTAssertTrue(text.contains("?"), "подсказка должна спрашивать, а не утверждать: \(text)")
    }
}
