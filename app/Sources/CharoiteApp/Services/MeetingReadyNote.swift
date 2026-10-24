import Foundation

#if os(macOS)

/// Пометка готовой встречи — «имена не определены», «пересборка не
/// завершена…» (№500). Вынесено из MeetingProcessingService: там политика
/// статуса и сервис, и файл уходил за тысячу строк.
extension MeetingProcessingPolicy {
    /// Коды устранимого отказа пересборки — те, что пишет конвейер
    /// (`rebuild_transcript.RebuildSkipped`).
    static let rebuildSkipCodes: Set<String> = ["recording_not_ready", "channel_lost", "failed"]

    /// Пометка готовой встречи — единственный источник слов для всех мест
    /// показа (№500). `nil` — пометки нет. Говорит об исходе, а не о файле.
    ///
    /// Состояние остаётся `ready`, и без пометки встреча с метками «Собеседник N»
    /// или с непересобранным черновиком выглядит ровно так же, как разобранная
    /// до конца.
    static func readyNote(for snapshot: MeetingProcessingSnapshot) -> String? {
        // Пересборка — первой: строка меню в одну строку усекает хвост, а
        // незавершённая пересборка важнее имён (Sonnet M4 выходного круга).
        var parts: [String] = []
        if let code = snapshot.rebuildSkipped {
            parts.append(rebuildSkipNote(code))
        }
        if snapshot.namesPending == true {
            parts.append(L.t("имена не определены", "speakers unnamed", "未识别出姓名"))
        }
        return parts.isEmpty ? nil : parts.joined(separator: "; ")
    }

    private static func rebuildSkipNote(_ code: String) -> String {
        switch code {
        case "recording_not_ready":
            return L.t("пересборка не завершена: записи ещё не готовы",
                       "rebuild not finished: recordings not ready yet",
                       "重建未完成：录音尚未就绪")
        case "channel_lost":
            return L.t("пересборка не завершена: канал записи не размечен",
                       "rebuild not finished: a recording channel was not diarized",
                       "重建未完成：录音声道未完成说话人标注")
        case "failed":
            return L.t("пересборка не завершена: сбой",
                       "rebuild not finished: failure",
                       "重建未完成：出错")
        default:
            return L.t("пересборка не завершена", "rebuild not finished", "重建未完成")
        }
    }

    /// Строка облачной ревизии в карточке встречи (№779): сколько её правок
    /// легло в граф. Считается из живого снимка в теле вида, как `readyNote`:
    /// ревизия кончается после «готово», и кэш карточки её не догнал бы.
    /// `nil` — итога нет (ревизии не было, она идёт или ничего не перенесла
    /// до этой версии). Число — перенесённые правки, а не тронутые в копии.
    static func cloudReviewLine(for snapshot: MeetingProcessingSnapshot) -> String? {
        guard let applied = snapshot.review?.result?.applied else { return nil }
        return L.t("Ревизия Claude: правок графа — \(applied)",
                   "Claude review: \(applied) graph edits",
                   "Claude 复审：图谱修改 \(applied) 处")
    }

    /// «Готово» — или «Готово, <пометка>».
    static func readyText(for snapshot: MeetingProcessingSnapshot) -> String {
        guard let note = readyNote(for: snapshot) else {
            return L.t("Готово", "Ready", "已完成")
        }
        return L.t("Готово, ", "Ready, ", "已完成，") + note
    }

    /// «Встреча готова» — или «Встреча готова — <пометка>»: те же слова, что
    /// у строки меню (`MenuLine.ready`), один источник (№500).
    static func readyStatusText(for snapshot: MeetingProcessingSnapshot) -> String {
        MenuLine.ready(note: readyNote(for: snapshot)).text
    }

    /// Предлагать ли рядом с пометкой «Пересобрать результат»: только при
    /// известном коде устранимого отказа. Неизвестный код (новее приложения)
    /// и одни «имена не определены» кнопки не дают.
    static func offersRebuild(for snapshot: MeetingProcessingSnapshot) -> Bool {
        snapshot.rebuildSkipped.map(rebuildSkipCodes.contains) ?? false
    }
}

extension MeetingProcessingSnapshot {
    /// Поле `review` статуса: этап (`running`, `ok`, `failed`…) и итог —
    /// сколько правок облака легло в граф. Итог пишет воркер по сверке, а не
    /// строка лога: лог сменил «правок графа» на «правок облака», и карточка
    /// молчала два месяца. Терпим, как `State`: кривое поле — пустой этап,
    /// а не потеря всей встречи из окна.
    struct ReviewStage: Decodable, Equatable, Sendable {
        struct Result: Decodable, Equatable, Sendable {
            let touched: Int
            let applied: Int
        }

        let state: String?
        let result: Result?

        init(state: String?, result: Result?) {
            self.state = state
            self.result = result
        }

        init(from decoder: Decoder) throws {
            let c = try? decoder.container(keyedBy: CodingKeys.self)
            state = try? c?.decode(String.self, forKey: .state)
            result = try? c?.decode(Result.self, forKey: .result)
        }

        enum CodingKeys: String, CodingKey {
            case state
            case result
        }
    }
}

#endif
