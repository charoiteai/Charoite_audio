import Foundation

#if os(macOS)

/// Карточка готовой встречи: результат внутри приложения.
///
/// «Встреча готова» приложение говорило давно — а дальше отправляло человека
/// разбираться с markdown-файлом. Карточка закрывает этот последний разрыв:
/// тема, длительность, участники, суть, решения и поручения видны на месте,
/// копируются в письмо одним нажатием, а файлы остаются на расстоянии кнопки.
struct MeetingCard: Equatable {
    var gist: String?
    /// Подробный протокол из Минуток: темы, полные формулировки решений,
    /// сроки поручений. Саммари даёт по строке на пункт — этого мало, чтобы
    /// вспомнить встречу неделю спустя.
    var minutes: MeetingMinutes?
    var decisions: [String] = []
    var tasks: [String] = []
    var openQuestions: [String] = []
    var participants: [String] = []
    var durationText: String?
    var summaryMissing = false
    var archiveFolder: URL?
    var obsidianURL: URL?
    /// Итог облачной ревизии из её лога: (правок графа, ревизия сохранена).
    /// nil — ревизии не было (облако выключено или лога нет).
    var cloudReview: CloudReviewResult?
}

struct CloudReviewResult: Equatable {
    let edits: Int
    let saved: Bool
}

struct MeetingManifest: Decodable, Equatable {
    let schemaVersion: Int
    let meetingID: String
    let title: String
    let durationMinutes: Int?
    let participants: [String]
    let summary: String?
    let decisions: [String]
    let actionItems: [String]
    let openQuestions: [String]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case meetingID = "meeting_id"
        case title
        case durationMinutes = "duration_minutes"
        case participants
        case summary
        case decisions
        case actionItems = "action_items"
        case openQuestions = "open_questions"
    }
}

extension MeetingProcessingSnapshot: Identifiable {
    var id: String { meetingID }
}

enum MeetingCardLoader {
    /// Собрать карточку из файлов встречи. Всё уже лежит на диске:
    /// стенограмма (участники, длительность), Саммари в архивной папке
    /// (суть, решения, поручения), заметка графа (куда вести Obsidian).
    static func load(for snapshot: MeetingProcessingSnapshot) -> MeetingCard {
        var card = MeetingCard()
        if let transcript = try? String(
            contentsOfFile: snapshot.transcriptPath, encoding: .utf8) {
            card.participants = participants(fromTranscript: transcript)
            card.durationText = durationText(fromTranscript: transcript)
        }
        guard let notePath = snapshot.notePath else { return card }
        let note = URL(fileURLWithPath: notePath)
        let graph = note.deletingLastPathComponent().deletingLastPathComponent()
        card.obsidianURL = obsidianURL(noteURL: note)
        let stamp = String(snapshot.meetingID.prefix(15))
        card.archiveFolder = archiveFolder(graph: graph, stamp: stamp)
        if let folder = card.archiveFolder {
            if let manifest = manifest(in: folder) {
                if card.participants.isEmpty { card.participants = manifest.participants }
                if card.durationText == nil, let minutes = manifest.durationMinutes {
                    card.durationText = durationText(minutes: minutes)
                }
                card.gist = manifest.summary
                card.decisions = manifest.decisions
                card.tasks = manifest.actionItems
                card.openQuestions = manifest.openQuestions
            }
            if let summary = try? String(
                contentsOf: folder.appendingPathComponent("Саммари.md"),
                encoding: .utf8) {
                if card.gist == nil { card.gist = gist(fromSummary: summary) }
                if card.decisions.isEmpty {
                    card.decisions = items(inSection: "Решили", of: summary)
                }
                if card.tasks.isEmpty {
                    card.tasks = items(inSection: "Поручения", of: summary)
                }
                if card.openQuestions.isEmpty {
                    card.openQuestions = items(inSection: "Открытые вопросы", of: summary)
                }
            } else if card.gist == nil {
                card.summaryMissing = true
            }
            if let text = try? String(
                contentsOf: folder.appendingPathComponent("Минутки.md"),
                encoding: .utf8) {
                let parsed = MeetingMinutes.parse(text)
                if !parsed.isEmpty { card.minutes = parsed }
            }
        } else {
            card.summaryMissing = true
        }
        let log = AppSettings.charoiteRoot
            .appendingPathComponent("logs/cloud_review_\(stamp).log")
        if let text = try? String(contentsOf: log, encoding: .utf8) {
            card.cloudReview = cloudReview(fromLog: text)
        }
        return card
    }

    static func manifest(in folder: URL) -> MeetingManifest? {
        let file = folder.appendingPathComponent("meeting.meta.json")
        guard let data = try? Data(contentsOf: file) else { return nil }
        return try? JSONDecoder().decode(MeetingManifest.self, from: data)
    }

    static func durationText(minutes: Int) -> String? {
        guard minutes > 0 else { return nil }
        let hours = minutes / 60
        let rest = minutes % 60
        if hours > 0 {
            return L.t("\(hours) ч \(String(format: "%02d", rest)) мин",
                       "\(hours) h \(String(format: "%02d", rest)) min",
                       "\(hours) 小时 \(String(format: "%02d", rest)) 分")
        }
        return L.t("\(rest) мин", "\(rest) min", "\(rest) 分钟")
    }

    // MARK: - Лог облачной ревизии

    /// Итог ревизии из logs/cloud_review_<штамп>.log — лог только читаем.
    ///
    /// В логе бывает несколько прогонов (встреча дорабатывалась) — правдой
    /// считается ПОСЛЕДНЯЯ строка «правок графа: N» и последняя строка про
    /// судьбу ревизии: «ревизия сохранена» / «НЕ сохранена». Без строки о
    /// правках итога нет — ревизия ещё идёт или упала до разбора.
    static func cloudReview(fromLog text: String) -> CloudReviewResult? {
        var edits: Int?
        var saved = false
        for line in text.split(separator: "\n") {
            if let range = line.range(of: "правок графа: ") {
                let tail = line[range.upperBound...]
                let digits = tail.prefix(while: \.isNumber)
                if let n = Int(digits) { edits = n }
            }
            if line.contains("ревизия сохранена") { saved = true }
            if line.contains("ревизия НЕ сохранена") { saved = false }
        }
        guard let edits else { return nil }
        return CloudReviewResult(edits: edits, saved: saved)
    }

    // MARK: - Стенограмма

    /// Строка «Участники (звучали в разговоре): …» — её пишет конвейер.
    static func participants(fromTranscript text: String) -> [String] {
        for line in text.split(separator: "\n", omittingEmptySubsequences: false).prefix(6) {
            guard line.hasPrefix("Участники") || line.hasPrefix("Participants"),
                  let colon = line.firstIndex(of: ":") else { continue }
            return line[line.index(after: colon)...]
                .split(separator: ",")
                .map { $0.trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }
        }
        return []
    }

    /// Длительность — из таймкодов реплик: от первой до последней.
    ///
    /// Отдельного поля с длительностью у встречи нет, а таймкоды есть у каждой
    /// реплики. Полночный переход учитывается: встреча в 23:58 не должна
    /// длиться «минус день».
    static func durationText(fromTranscript text: String) -> String? {
        guard let re = try? NSRegularExpression(pattern: #"(\d{1,2}):(\d{2})"#) else {
            return nil
        }
        var minutes: [Int] = []
        for line in text.split(separator: "\n") where line.hasPrefix("**") {
            guard let open = line.firstIndex(of: "[") else { continue }
            let tail = String(line[open...])
            let range = NSRange(tail.startIndex..., in: tail)
            re.enumerateMatches(in: tail, range: range) { match, _, _ in
                guard let match,
                      let hr = Range(match.range(at: 1), in: tail),
                      let mr = Range(match.range(at: 2), in: tail),
                      let h = Int(tail[hr]), let mm = Int(tail[mr]),
                      h < 24, mm < 60 else { return }
                minutes.append(h * 60 + mm)
            }
        }
        guard let first = minutes.first, let last = minutes.last else { return nil }
        var span = last - first
        if span < 0 { span += 24 * 60 }
        guard span > 0 else { return nil }
        let h = span / 60, m = span % 60
        if h > 0 {
            return L.t("\(h) ч \(String(format: "%02d", m)) мин",
                       "\(h) h \(String(format: "%02d", m)) min",
                       "\(h) 小时 \(String(format: "%02d", m)) 分")
        }
        return L.t("\(m) мин", "\(m) min", "\(m) 分钟")
    }

    // MARK: - Саммари

    /// «**Суть одной строкой:** …» — первая строка, ради которой саммари и есть.
    static func gist(fromSummary text: String) -> String? {
        let marker = "Суть одной строкой:**"
        for line in text.split(separator: "\n") {
            guard let range = line.range(of: marker) else { continue }
            let value = line[range.upperBound...].trimmingCharacters(in: .whitespaces)
            return value.isEmpty ? nil : value
        }
        return nil
    }

    /// Пункты раздела «## <имя>» до следующего раздела.
    static func items(inSection name: String, of text: String) -> [String] {
        guard let head = text.range(of: "## \(name)") else { return [] }
        let body = text[head.upperBound...]
        let end = body.range(of: "\n## ")?.lowerBound
            ?? body.range(of: "\n---")?.lowerBound
            ?? body.endIndex
        return body[..<end]
            .split(separator: "\n")
            .filter { $0.hasPrefix("- ") }
            .map { $0.dropFirst(2).trimmingCharacters(in: .whitespaces) }
            // «решений не было» — честный пустой раздел, а не пункт списка
            .filter { !$0.lowercased().contains("не было") && $0.lowercased() != "нет" }
    }

    // MARK: - Куда вести

    /// Папка встречи в «Встречи-архив»: ищется по дате и времени в начале
    /// имени — тема в хвосте меняется переименованием, встреча нет.
    static func archiveFolder(graph: URL, stamp: String) -> URL? {
        guard stamp.count >= 15 else { return nil }
        let day = String(stamp.prefix(10))
        let hh = String(stamp.dropFirst(11).prefix(2))
        let mm = String(stamp.dropFirst(13).prefix(2))
        let prefix = "\(day) \(hh)-\(mm) "
        let archive = graph.appendingPathComponent("Встречи-архив")
        let dirs = (try? FileManager.default.contentsOfDirectory(
            at: archive, includingPropertiesForKeys: nil)) ?? []
        return dirs.first { $0.lastPathComponent.hasPrefix(prefix) }
    }

    /// obsidian://open на заметку встречи — тот же адрес, что кладёт архивная
    /// папка в «Открыть в Obsidian.command».
    static func obsidianURL(noteURL: URL) -> URL? {
        let graph = noteURL.deletingLastPathComponent().deletingLastPathComponent()
        let vault = graph.deletingLastPathComponent().lastPathComponent
        let stem = noteURL.deletingPathExtension().lastPathComponent
        var parts = URLComponents()
        parts.scheme = "obsidian"
        parts.host = "open"
        parts.queryItems = [
            URLQueryItem(name: "vault", value: vault),
            URLQueryItem(name: "file", value: "\(graph.lastPathComponent)/Встречи/\(stem)"),
        ]
        return parts.url
    }

    // MARK: - Что уходит в буфер

    /// Резюме для письма: суть и решения — то, что пересылают чаще всего.
    static func summaryText(title: String, card: MeetingCard) -> String {
        var out = [title]
        if let gist = card.gist { out.append(gist) }
        if !card.decisions.isEmpty {
            out.append("")
            out.append(L.t("Решили:", "Decided:", "决定："))
            out += card.decisions.map { "- \($0)" }
        }
        return out.joined(separator: "\n")
    }

    static func tasksText(card: MeetingCard) -> String {
        card.tasks.map { "- \($0)" }.joined(separator: "\n")
    }

    static func fullText(title: String, dateText: String, card: MeetingCard) -> String {
        var out = ["\(title) — \(dateText)"]
        if let d = card.durationText { out[0] += " · \(d)" }
        if !card.participants.isEmpty {
            out.append(L.t("Участники: ", "Participants: ", "参会者：")
                       + card.participants.joined(separator: ", "))
        }
        if let gist = card.gist { out.append(""); out.append(gist) }
        if !card.decisions.isEmpty {
            out.append(""); out.append(L.t("Решили:", "Decided:", "决定："))
            out += card.decisions.map { "- \($0)" }
        }
        if !card.tasks.isEmpty {
            out.append(""); out.append(L.t("Поручения:", "Action items:", "任务："))
            out += card.tasks.map { "- \($0)" }
        }
        if !card.openQuestions.isEmpty {
            out.append(""); out.append(L.t("Открытые вопросы:", "Open questions:", "待解决问题："))
            out += card.openQuestions.map { "- \($0)" }
        }
        return out.joined(separator: "\n")
    }
}

/// Команда переименования — venv-питон и скрипт, как у повтора обработки.
enum MeetingRenameCommand {
    static func build(root: URL, meetingID: String, title: String) -> (exec: URL, args: [String]) {
        let python = AppSettings.pythonExecutable(root: root).path
        let script = root.appendingPathComponent("scripts/rename_meeting.py").path
        return (
            exec: URL(fileURLWithPath: python),
            args: [script, String(meetingID.prefix(15)), title, "--yes"]
        )
    }
}
#endif
