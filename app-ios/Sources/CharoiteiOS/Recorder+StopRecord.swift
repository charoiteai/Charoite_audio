import Foundation

/// Почему запись остановилась — значение, а не строка для экрана (№200).
///
/// 07.09 одно собрание приехало на Mac тремя кусками без звонка, и причины на
/// диске не было: `stop()` освобождал сессию и отдавал файл, `lastStopReason`
/// жил только на экране и умирал с процессом. Теперь у каждого закрытого файла
/// есть манифест `<файл>.json` рядом — кто закрыл, почему, когда и сколько
/// секунд, — и он едет на Mac той же парой, что аудио (`Inbox`). Сирота без
/// манифеста получает `noStop`: факт «стопа не было», не догадка «процесс
/// убит» (входной круг DS и GLM по №200).
extension Recorder {
    /// Закрытый список: причина существует только вместе с точкой кода, которая
    /// её ставит. `disk_full` сюда не входит — переполнение диска в записи
    /// ничем не отличается от сбоя кодека, отдельной детекции нет.
    enum StopReason: String, Codable, CaseIterable, Sendable {
        case user                              // кнопка Стоп
        case callNoResume = "call_no_resume"   // микрофон не вернулся за бюджет после звонка → ротация
        case mediaReset = "media_reset"        // перезапуск аудиослужбы → ротация
        case encodeError = "encode_error"      // сбой кодека: ротация, серия подряд — стоп
        case stalled                           // файл не растёт, resume не помог → ротация
        case noStop = "no_stop"                // сирота в current/: стоп не зафиксирован

        /// Строка для экрана — производная значения, не наоборот.
        var text: String { text(terminal: true) }

        /// Терминальный стоп и ротация одной причины звучат по-разному: у ротации
        /// встреча продолжена новым файлом, у стопа — остановлена. Счёт сбоев кодека —
        /// из политики, а не строкой сбоку (Minor DS выходного круга по №200).
        func text(terminal: Bool) -> String {
            switch self {
            case .encodeError where terminal:
                let n = Recorder.maxEncodeErrors
                return L.t("Сбой записи (кодек) \(n) раза подряд — запись остановлена",
                           "Recording error (codec) \(n) times in a row — recording stopped",
                           "录音错误（编解码器）连续 \(n) 次 — 录音已停止")
            default:
                break
            }
            switch self {
            case .user:
                return L.t("Остановлена вручную", "Stopped manually", "手动停止")
            case .callNoResume:
                return L.t("Микрофон не вернулся за минуту после звонка — файл закрыт, встреча продолжена новым",
                           "Microphone did not come back within a minute after the call — file closed, meeting continued in a new one",
                           "通话后一分钟内麦克风未恢复 — 文件已关闭，会议以新文件继续")
            case .mediaReset:
                return L.t("Аудиослужба перезапущена — файл сохранён, встреча продолжена новым",
                           "Audio service reset — file kept, meeting continued in a new one",
                           "音频服务已重置 — 文件已保留，会议以新文件继续")
            case .encodeError:
                return L.t("Сбой записи (кодек) — файл закрыт",
                           "Recording error (codec) — file closed",
                           "录音错误（编解码器）— 文件已关闭")
            case .stalled:
                return L.t("Запись не поднялась — файл закрыт, встреча продолжена новым",
                           "Could not resume — file closed, meeting continued in a new one",
                           "无法恢复 — 文件已关闭，会议以新文件继续")
            case .noStop:
                return L.t("Стоп не зафиксирован — приложение не закрыло файл",
                           "Stop not recorded — the app did not close the file",
                           "未记录停止 — 应用未关闭文件")
            }
        }
    }

    /// Манифест закрытого файла. Пишется в `stop(reason:)` рядом с аудио и
    /// едет с ним парой; на Mac превращается в событие следа записи.
    struct StopRecord: Codable, Equatable {
        enum Kind: String, Codable { case stop, rotate }

        var kind: Kind
        var reason: StopReason
        var at: Date
        var seconds: TimeInterval?
        /// Стем первого файла серии: куски одной встречи после ротаций узнают
        /// друг друга (склейка на Mac — отдельная карточка, здесь только поле).
        var series: String?
        /// Делегат сообщил, что контейнер не финализирован — файл может быть
        /// неполным. Ставится по URL своего файла, не по текущему рекордеру.
        var finalizedOK: Bool?

        static let version = 1

        private enum CodingKeys: String, CodingKey {
            case kind, reason, at, seconds, series
            case finalizedOK = "finalized_ok"
            case version
        }

        init(kind: Kind, reason: StopReason, at: Date, seconds: TimeInterval?, series: String?,
             finalizedOK: Bool? = nil) {
            self.kind = kind
            self.reason = reason
            self.at = at
            self.seconds = seconds
            self.series = series
            self.finalizedOK = finalizedOK
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            kind = try c.decode(Kind.self, forKey: .kind)
            reason = try c.decode(StopReason.self, forKey: .reason)
            at = try c.decode(Date.self, forKey: .at)
            seconds = try c.decodeIfPresent(TimeInterval.self, forKey: .seconds)
            series = try c.decodeIfPresent(String.self, forKey: .series)
            finalizedOK = try c.decodeIfPresent(Bool.self, forKey: .finalizedOK)
        }

        func encode(to encoder: Encoder) throws {
            var c = encoder.container(keyedBy: CodingKeys.self)
            try c.encode(kind, forKey: .kind)
            try c.encode(reason, forKey: .reason)
            try c.encode(at, forKey: .at)
            try c.encodeIfPresent(seconds, forKey: .seconds)
            try c.encodeIfPresent(series, forKey: .series)
            try c.encodeIfPresent(finalizedOK, forKey: .finalizedOK)
            try c.encode(Self.version, forKey: .version)
        }

        private static let encoder: JSONEncoder = {
            let e = JSONEncoder()
            e.dateEncodingStrategy = .iso8601
            e.outputFormatting = [.sortedKeys]
            return e
        }()

        private static let decoder: JSONDecoder = {
            let d = JSONDecoder()
            d.dateDecodingStrategy = .iso8601
            return d
        }()

        func write(nextTo audio: URL) throws {
            try Self.encoder.encode(self).write(to: Inbox.sidecar(for: audio), options: .atomic)
        }

        static func read(nextTo audio: URL) -> StopRecord? {
            guard let data = try? Data(contentsOf: Inbox.sidecar(for: audio)) else { return nil }
            return try? decoder.decode(StopRecord.self, from: data)
        }

        /// Делегат финализации: отметить в манифесте СВОЕГО файла, что контейнер
        /// закрылся с ошибкой. Файл к этому моменту мог уехать из `current/` в
        /// очередь — ищем по имени в обеих папках, а не в `lastResult` нового
        /// рекордера (Critical DS входного круга).
        static func markUnfinalized(audio: URL, searching dirs: [URL]? = nil) {
            for dir in dirs ?? [audio.deletingLastPathComponent(), Inbox.inProgress, Inbox.outbox] {
                let candidate = dir.appendingPathComponent(audio.lastPathComponent)
                guard var rec = read(nextTo: candidate) else { continue }
                rec.finalizedOK = false
                try? rec.write(nextTo: candidate)
                return
            }
        }
    }
}
