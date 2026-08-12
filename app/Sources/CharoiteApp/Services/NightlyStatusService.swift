import Combine
import Foundation

#if os(macOS)

/// Прошёл ли ночной цикл — и когда.
///
/// Ночью Чароит причёсывает граф: ревизия ядер, досье, дедуп файлов,
/// утренний бриф. Работа невидимая по определению — человек спит. Узнать,
/// что она не выполняется, раньше можно было только заглянув в
/// `/tmp/charoite_nightly.log`, который к тому же стирается перезагрузкой:
/// «лога нет» и «ночью ничего не делалось» выглядели одинаково.
///
/// Поэтому `scripts/nightly.sh` пишет машиночитаемый итог в
/// `logs/nightly.json` рядом с данными, а этот сервис его читает.
enum NightlyState: Equatable {
    /// Идёт прямо сейчас.
    ///
    /// Цикл не всегда быстрый: ревизия ядер и досье по всем графам — это
    /// локальная модель на каждой теме, а облачный проход добавляет по
    /// вызову на досье. Первый же прогон занял больше часа, и без этого
    /// состояния приложение всё это время честно докладывало бы, что
    /// ночная обработка не запускалась.
    case running(started: Date)
    /// Отработал этой ночью без ошибок.
    case ok(finished: Date)
    /// Отработал, но какие-то шаги упали.
    case failed(finished: Date, steps: [String])
    /// Прогон начался и не завершился: машину усыпили или перезагрузили.
    case interrupted(started: Date)
    /// Последний известный прогон старше суток.
    case stale(finished: Date)
    /// Статуса нет вовсе: цикл ни разу не отрабатывал либо не настроен.
    case never
    /// launchd запускает ночной цикл не из рабочей папки.
    ///
    /// Самое тихое из всех: прогон идёт, лог пишется, «всё работает» — а
    /// граф каждую ночь правит другая копия кода. У автора агент две недели
    /// запускал скрипт из папки, оставленной после переезда репозитория:
    /// в той версии ревизия ядер сливала дубли безусловно, минуя настройку
    /// из конфига. Заметить это по внешним признакам невозможно.
    case foreignScript(path: String)
}

struct NightlyStatus: Equatable {
    let state: NightlyState

    /// Разбор статуса и решение о свежести — в одном месте и без обращения к
    /// диску, чтобы правило проверялось тестом, а не ночью.
    static func from(json: [String: Any], now: Date = Date()) -> NightlyStatus {
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd HH:mm:ss"
        fmt.locale = Locale(identifier: "en_US_POSIX")

        let state = json["state"] as? String ?? ""
        let started = (json["started"] as? String).flatMap(fmt.date(from:))
        let finished = (json["finished"] as? String).flatMap(fmt.date(from:))

        if state == "interrupted" {
            return NightlyStatus(state: .interrupted(started: started ?? now))
        }
        if state == "running" {
            let began = started ?? now
            // Шесть часов — заведомо больше любого честного прогона. Дольше
            // — значит процесс убили так, что записать «прервано» он не
            // успел (kill -9, отключение питания): вечное «идёт» скрывало бы
            // ровно ту поломку, ради которой всё это и заведено.
            return NightlyStatus(state: now.timeIntervalSince(began) > 6 * 3600
                                 ? .interrupted(started: began)
                                 : .running(started: began))
        }
        guard let finished else { return NightlyStatus(state: .never) }

        // Сутки с запасом: цикл в 04:15, а человек смотрит и утром, и вечером.
        // 26 часов — «прошлой ночи ещё не было», 27 — «ночь пропущена».
        if now.timeIntervalSince(finished) > 26 * 3600 {
            return NightlyStatus(state: .stale(finished: finished))
        }
        if state == "ok" {
            return NightlyStatus(state: .ok(finished: finished))
        }
        let steps = ((json["failed"] as? String) ?? "")
            .split(separator: " ").map(String.init)
        return NightlyStatus(state: .failed(finished: finished, steps: steps))
    }

    /// Путь к `nightly.sh`, прописанный в launchd-агенте.
    ///
    /// Label агента не угадываем: он свой у каждой установки (в документации
    /// `ai.charoite.nightly`, у автора исторический `ru.charoit.tier3`).
    /// Ищем по содержимому — так проверка переживёт переименование.
    static func agentScriptPath(inAgentsAt dir: URL) -> String? {
        guard let items = try? FileManager.default.contentsOfDirectory(
            at: dir, includingPropertiesForKeys: nil) else { return nil }
        for plist in items where plist.pathExtension == "plist" {
            guard let text = try? String(contentsOf: plist, encoding: .utf8),
                  text.contains("nightly.sh") else { continue }
            // Ищем тот `<string>`, внутри которого сам скрипт, а не первый в
            // строке: в `ProgramArguments` перед ним стоит `/bin/bash`, и в
            // однострочном `<array>` — как в нашей же документации — по строкам
            // нашёлся бы именно интерпретатор. Тогда путь не совпал бы никогда,
            // и приложение постоянно жаловалось бы на подмену скрипта.
            var rest = text.startIndex..<text.endIndex
            while let open = text.range(of: "<string>", range: rest),
                  let close = text.range(of: "</string>",
                                         range: open.upperBound..<text.endIndex) {
                let value = String(text[open.upperBound..<close.lowerBound])
                if value.hasSuffix("nightly.sh") { return value }
                rest = close.upperBound..<text.endIndex
            }
        }
        return nil
    }

    /// Тот ли это скрипт, которым человек считает свою установку.
    ///
    /// Сравниваем разрешённые пути: `~/Project/charoite` и симлинк на неё —
    /// одна и та же установка, а ругаться на симлинк значит приучить к
    /// ложной тревоге. Агента нет вовсе — не наше дело: цикл просто не
    /// настроен, об этом скажет `never`.
    static func agentPointsElsewhere(agentScript: String?, root: URL) -> Bool {
        guard let agentScript, !agentScript.isEmpty else { return false }
        let expected = root.appendingPathComponent("scripts/nightly.sh")
        return URL(fileURLWithPath: agentScript).resolvingSymlinksInPath().path
            != expected.resolvingSymlinksInPath().path
    }
}

@MainActor
final class NightlyStatusService: ObservableObject {
    static let shared = NightlyStatusService()

    @Published private(set) var status = NightlyStatus(state: .never)

    private init() { refresh() }

    static var statusURL: URL {
        AppSettings.charoiteRoot.appendingPathComponent("logs/nightly.json")
    }

    func refresh() {
        // Чужой скрипт важнее любого статуса: если ночью работает не наш
        // код, содержимое nightly.json описывает не ту установку, которую
        // человек видит на экране.
        let agents = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents")
        let script = NightlyStatus.agentScriptPath(inAgentsAt: agents)
        if NightlyStatus.agentPointsElsewhere(agentScript: script,
                                              root: AppSettings.charoiteRoot) {
            status = NightlyStatus(state: .foreignScript(path: script ?? ""))
            return
        }
        guard let data = try? Data(contentsOf: Self.statusURL),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            status = NightlyStatus(state: .never)
            return
        }
        status = NightlyStatus.from(json: json)
    }

    // MARK: - Как это выглядит на «Сегодня»

    var title: String {
        switch status.state {
        case .running:
            return L.t("Ночная обработка идёт", "Nightly pass is running", "夜间处理进行中")
        case .ok:
            return L.t("Ночная обработка пройдена", "Nightly pass completed", "夜间处理已完成")
        case .failed:
            return L.t("Ночная обработка с ошибками", "Nightly pass had errors", "夜间处理出现错误")
        case .interrupted:
            return L.t("Ночная обработка прервана", "Nightly pass interrupted", "夜间处理被中断")
        case .stale:
            return L.t("Ночная обработка пропущена", "Nightly pass skipped", "夜间处理已跳过")
        case .never:
            return L.t("Ночная обработка не запускалась", "Nightly pass never ran", "夜间处理从未运行")
        case .foreignScript:
            return L.t("Ночная обработка идёт из другой папки",
                       "Nightly pass runs from another folder",
                       "夜间处理来自其他目录")
        }
    }

    var detail: String {
        let time = DateFormatter()
        time.dateFormat = "HH:mm"
        let day = DateFormatter()
        day.dateFormat = "d MMMM"
        day.locale = L.locale

        switch status.state {
        case .running(let started):
            return L.t("Начата \(time.string(from: started)) — ядра, досье, бриф",
                       "Started at \(time.string(from: started)) — cores, dossiers, brief",
                       "\(time.string(from: started)) 开始 —— 内核、档案、简报")
        case .ok(let finished):
            return L.t("Граф причёсан в \(time.string(from: finished))",
                       "Graph tidied at \(time.string(from: finished))",
                       "图谱已于 \(time.string(from: finished)) 整理完毕")
        case .failed(_, let steps):
            let list = steps.joined(separator: ", ")
            return L.t("Не отработало: \(list). Подробности — в /tmp/charoite_nightly.log",
                       "Failed steps: \(list). Details in /tmp/charoite_nightly.log",
                       "失败步骤：\(list)。详情见 /tmp/charoite_nightly.log")
        case .interrupted(let started):
            return L.t("Начата \(time.string(from: started)) и не завершилась — машину усыпили или перезагрузили",
                       "Started at \(time.string(from: started)) and never finished — the machine slept or rebooted",
                       "于 \(time.string(from: started)) 开始但未完成 — 机器休眠或重启")
        case .stale(let finished):
            return L.t("Последний раз — \(day.string(from: finished)). Проверьте launchd-агент",
                       "Last run on \(day.string(from: finished)). Check the launchd agent",
                       "上次运行：\(day.string(from: finished))。请检查 launchd 代理")
        case .never:
            return L.t("Ядра, досье и утренний бриф не обновляются. Настройка — в SETUP.md",
                       "Cores, dossiers and the morning brief are not being updated. See SETUP.md",
                       "内核、档案与晨间简报不会更新。设置见 SETUP.md")
        case .foreignScript(let path):
            return L.t("launchd запускает \(path) — граф правит другая копия кода",
                       "launchd runs \(path) — the graph is edited by another copy of the code",
                       "launchd 运行的是 \(path) —— 图谱正被另一份代码修改")
        }
    }

    /// Показывать ли строку вообще. Успешный прогон — норма, о ней достаточно
    /// одной спокойной строки; всё остальное требует внимания.
    var needsAttention: Bool {
        switch status.state {
        case .ok, .running: return false
        case .failed, .interrupted, .stale, .never, .foreignScript: return true
        }
    }

    var icon: String {
        switch status.state {
        case .running: return "moon.stars"
        case .ok: return "moon.stars.fill"
        case .failed: return "exclamationmark.triangle.fill"
        case .interrupted: return "pause.circle.fill"
        case .stale, .never: return "moon.zzz"
        case .foreignScript: return "arrow.triangle.branch"
        }
    }
}

#endif
