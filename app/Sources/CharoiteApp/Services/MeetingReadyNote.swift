import Foundation

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
        var parts: [String] = []
        if snapshot.namesPending == true {
            parts.append(L.t("имена не определены", "speakers unnamed", "未识别出姓名"))
        }
        if let code = snapshot.rebuildSkipped {
            parts.append(rebuildSkipNote(code))
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
