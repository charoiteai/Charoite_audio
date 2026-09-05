import Foundation

/// Одна запись папки импорта — как её видит вкладка «Внешняя запись».
///
/// Источник правды — файловая система и сайдкары, которые пишет
/// `scripts/import_meeting.py`: метка `.<имя>.import-error` у сбойного файла в
/// корне папки, `.<имя>.imported.json` у копии в `done/`. Swift ничего не
/// решает сам — только читает и показывает.
struct ImportItem: Identifiable, Equatable {
    enum Phase: Equatable {
        /// Лежит в корне папки: сканер ещё не брал (или ждёт покоя размера WAV).
        case waiting
        /// Прошлый импорт не удался — метка ошибки; файл на месте, не удаляется.
        case failed(message: String)
        /// В done/ с сайдкаром импорта.
        case done(Imported)
        /// В done/ без сайдкара — импорт до этой версии; срок назначит
        /// первая уборка (скрипт запишет сайдкар «увидели сейчас» с
        /// `legacy: true` — тогда дата уже есть, а «встреча собрана» — нет:
        /// что это за файл, Charoite не знает).
        case legacy(deleteAt: Date?)
    }

    struct Imported: Equatable {
        var stamp: String?
        var transcript: String?
        var kind: String
        var importedAt: Date
        var deleteAt: Date
        var isRepeat: Bool
        var noSpeech: Bool
    }

    let url: URL
    let name: String
    let bytes: Int
    let recorded: Date
    let phase: Phase
    var id: String { url.path }
}

/// Правила вкладки, вынесенные из вью: срок удаления, подписи, порядок.
/// Проверяются тестом, а не глазами (ExternalRecordingPolicyTests).
enum ExternalRecordingPolicy {
    /// Зеркало `audio.import_keep_days` по умолчанию — для легаси-копий без
    /// сайдкара, у которых срок считает уже не скрипт, а мы.
    static let defaultKeepDays: Double = 2

    /// Зеркало AUDIO|TEXT|SUBS скрипта — единственного судьи форматов.
    static let supported: Set<String> = [
        "m4a", "wav", "mp3", "aif", "aiff", "caf", "txt", "md", "vtt", "srt",
    ]
    static let audio: Set<String> = ["m4a", "wav", "mp3", "aif", "aiff", "caf"]

    /// Сайдкар `.<имя>.imported.json` — что пишет скрипт при переносе в done/.
    struct Sidecar: Decodable {
        var imported_at: Double
        var delete_after: Double?
        var keep_days: Double?
        var stamp: String?
        var transcript: String?
        var kind: String?
        var `repeat`: Bool?
        var no_speech: Bool?
        var legacy: Bool?
    }

    /// Метка `.<имя>.import-error`.
    struct ErrorMarker: Decodable {
        var failed_at: Double?
        var code: Int?
        var message: String?
    }

    static func imported(from sidecar: Sidecar) -> ImportItem.Imported {
        let importedAt = Date(timeIntervalSince1970: sidecar.imported_at)
        let deleteAt = sidecar.delete_after.map { Date(timeIntervalSince1970: $0) }
            ?? importedAt.addingTimeInterval((sidecar.keep_days ?? defaultKeepDays) * 86400)
        return .init(stamp: sidecar.stamp,
                     transcript: sidecar.transcript,
                     kind: sidecar.kind ?? "meeting",
                     importedAt: importedAt,
                     deleteAt: deleteAt,
                     isRepeat: sidecar.repeat ?? false,
                     noSpeech: sidecar.no_speech ?? false)
    }

    static func isSupported(_ url: URL) -> Bool {
        supported.contains(url.pathExtension.lowercased())
    }

    /// Свободное имя для копии в папке импорта: чужой файл с тем же именем
    /// не затираем — диктофон телефона всё зовёт Recording.m4a.
    static func uniqueName(_ name: String, taken: Set<String>) -> String {
        guard taken.contains(name) else { return name }
        let ext = (name as NSString).pathExtension
        let stem = (name as NSString).deletingPathExtension
        var n = 1
        while true {
            let candidate = ext.isEmpty ? "\(stem)-\(n)" : "\(stem)-\(n).\(ext)"
            if !taken.contains(candidate) { return candidate }
            n += 1
        }
    }

    /// Порядок в списке: сбойные → ждущие → обработанные, свежие сверху.
    static func rank(_ phase: ImportItem.Phase) -> Int {
        switch phase {
        case .failed: return 0
        case .waiting: return 1
        case .done, .legacy: return 2
        }
    }

    static func sorted(_ items: [ImportItem]) -> [ImportItem] {
        items.sorted { a, b in
            let ra = rank(a.phase), rb = rank(b.phase)
            if ra != rb { return ra < rb }
            return sortDate(a) > sortDate(b)
        }
    }

    private static func sortDate(_ item: ImportItem) -> Date {
        if case .done(let imported) = item.phase { return imported.importedAt }
        return item.recorded
    }

    static func failedCount(_ items: [ImportItem]) -> Int {
        items.filter { if case .failed = $0.phase { return true } else { return false } }.count
    }

    /// Подпись состояния — одной строкой под именем файла.
    static func statusText(_ phase: ImportItem.Phase, now: Date = Date()) -> String {
        switch phase {
        case .waiting:
            return L.t("ждёт обработки", "waiting for processing", "等待处理")
        case .failed(let message):
            let head = L.t("не обработан", "failed", "处理失败")
            return message.isEmpty ? head : "\(head): \(message)"
        case .done(let imported):
            var parts: [String] = []
            if imported.noSpeech {
                parts.append(L.t("речи не найдено", "no speech found", "未检测到语音"))
            } else if imported.isRepeat {
                parts.append(L.t("уже была в архиве", "already in the archive", "已在归档中"))
            } else {
                switch imported.kind {
                case "note": parts.append(L.t("заметка обработана", "note processed", "笔记已处理"))
                case "diary": parts.append(L.t("дневник обработан", "diary processed", "日记已处理"))
                default: parts.append(L.t("встреча собрана", "meeting built", "会议已生成"))
                }
            }
            if let stamp = imported.stamp { parts.append(stamp) }
            parts.append(deletionText(deleteAt: imported.deleteAt, now: now))
            return parts.joined(separator: " · ")
        case .legacy(let deleteAt):
            return [L.t("обработан раньше", "processed earlier", "此前已处理"),
                    deleteAt.map { deletionText(deleteAt: $0, now: now) }
                        ?? L.t("срок назначит ближайшая проверка", "the next check sets the deadline", "下次检查将设定期限")]
                .joined(separator: " · ")
        }
    }

    /// «удалится 07.09» — срок стоит на виду, а не в конфиге.
    static func deletionText(deleteAt: Date, now: Date = Date()) -> String {
        if deleteAt <= now {
            return L.t("удалится при следующей проверке", "deleted at the next check", "下次检查时删除")
        }
        if Calendar.current.isDate(deleteAt, inSameDayAs: now) {
            return L.t("удалится сегодня", "deleted today", "今天删除")
        }
        let f = DateFormatter()
        f.locale = L.locale
        f.dateFormat = "dd.MM"
        let day = f.string(from: deleteAt)
        return L.t("удалится \(day)", "deleted on \(day)", "\(day) 删除")
    }
}
