import XCTest
@testable import CharoiteApp

/// Ночная обработка графа — работа, которую никто не видит.
///
/// Ночью правятся ядра, собираются досье, пишется утренний бриф. Если это
/// перестаёт выполняться, узнать неоткуда: граф просто медленно черствеет,
/// а лог launchd лежит в `/tmp` и исчезает при перезагрузке — «лога нет» и
/// «ночью ничего не делалось» выглядят одинаково.
///
/// Поэтому `nightly.sh` пишет итог в `logs/nightly.json`, а разбор статуса
/// живёт здесь и проверяется тестом, а не через месяц по несвежему графу.
final class NightlyStatusTests: XCTestCase {

    private let fmt: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()

    private func json(state: String, finishedAgo: TimeInterval,
                      failed: String = "", now: Date) -> [String: Any] {
        let finished = now.addingTimeInterval(-finishedAgo)
        return [
            "started": fmt.string(from: finished.addingTimeInterval(-600)),
            "finished": fmt.string(from: finished),
            "state": state,
            "rc": state == "ok" ? 0 : 1,
            "failed": failed,
        ]
    }

    func testSuccessfulPassTonight() {
        let now = Date()
        let s = NightlyStatus.from(json: json(state: "ok", finishedAgo: 5 * 3600, now: now),
                                   now: now)
        guard case .ok = s.state else { return XCTFail("ожидали успешный прогон: \(s.state)") }
    }

    func testFailedStepsAreListed() {
        let now = Date()
        let s = NightlyStatus.from(
            json: json(state: "failed", finishedAgo: 3 * 3600,
                       failed: "ревизия-ядер досье", now: now),
            now: now)
        guard case .failed(_, let steps) = s.state else {
            return XCTFail("ожидали неуспешный прогон: \(s.state)")
        }
        XCTAssertEqual(steps, ["ревизия-ядер", "досье"],
                       "человек должен видеть, что именно не отработало")
    }

    /// Самый опасный случай: цикл молча перестал запускаться.
    func testSkippedNightIsNoticed() {
        let now = Date()
        let s = NightlyStatus.from(json: json(state: "ok", finishedAgo: 50 * 3600, now: now),
                                   now: now)
        guard case .stale = s.state else {
            return XCTFail("пропущенная ночь выглядит как успех: \(s.state)")
        }
    }

    /// Граница: вечером того же дня прогон ещё свежий, а не «пропущен».
    func testEveningOfTheSameDayIsStillFresh() {
        let now = Date()
        let s = NightlyStatus.from(json: json(state: "ok", finishedAgo: 20 * 3600, now: now),
                                   now: now)
        guard case .ok = s.state else {
            return XCTFail("прогон этой ночи объявлен пропущенным: \(s.state)")
        }
    }

    /// Машину усыпили посреди прогона — это не успех и не пропуск.
    func testInterruptedRun() {
        let now = Date()
        var j = json(state: "interrupted", finishedAgo: 2 * 3600, now: now)
        j["rc"] = 0
        guard case .interrupted = NightlyStatus.from(json: j, now: now).state else {
            return XCTFail("оборванный прогон не распознан")
        }
    }

    func testNoStatusAtAll() {
        guard case .never = NightlyStatus.from(json: [:]).state else {
            return XCTFail("пустой статус должен читаться как «не запускалось»")
        }
    }

    /// Сторож проводки: скрипт обязан писать статус, иначе показывать нечего.
    func testNightlyScriptWritesStatus() throws {
        let script = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("scripts/nightly.sh")
        let text = try String(contentsOf: script, encoding: .utf8)

        XCTAssertTrue(text.contains("logs/nightly.json") || text.contains("nightly.json"),
                      "nightly.sh перестал писать статус — экран «Сегодня» ослеп")
        XCTAssertTrue(text.contains("write_status ok"),
                      "успешный прогон не отмечается")
        XCTAssertTrue(text.contains("write_status failed"),
                      "неуспешный прогон не отмечается — ошибки станут невидимыми")
    }
}
