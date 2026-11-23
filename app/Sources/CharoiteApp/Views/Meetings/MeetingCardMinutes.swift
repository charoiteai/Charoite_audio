import SwiftUI
import CryptoKit

#if os(macOS)

/// Only a verified conversation can mark a source excerpt as literal text.
enum MeetingCardFilePart: Equatable {
    case markdown(String)
    case transcript(String)

    var displayText: AttributedString {
        switch self {
        case .markdown(let line): return MarkdownLine.render(line)
        case .transcript(let source): return AttributedString(source)
        }
    }

    static func read(_ text: String, verifiedConversation: Bool) -> [Self] {
        // The writer adds LF delimiters. A source CR before a delimiter still belongs to the source.
        let lines = text.utf8.split(separator: 0x0A, omittingEmptySubsequences: false)
            .map { String(decoding: $0, as: UTF8.self) }
        var parts: [Self] = []
        var index = 0
        while index < lines.count {
            let line = lines[index]
            index += 1
            if !line.trimmingCharacters(in: .whitespaces).isEmpty {
                parts.append(.markdown(line))
            }
            guard verifiedConversation, line == "**Из стенограммы**",
                  index + 1 < lines.count, lines[index].isEmpty else { continue }
            let opener = lines[index + 1]
            let fence = opener.prefix(while: { $0 == "`" })
            guard fence.count >= 3, opener.dropFirst(fence.count) == "text" else { continue }
            let start = index + 2
            let end = lines[start...].firstIndex(of: String(fence)) ?? lines.endIndex
            parts.append(.transcript(lines[start..<end].joined(separator: "\n")))
            index = end < lines.endIndex ? end + 1 : end
        }
        return parts
    }
}

/// Четыре глубины чтения одной встречи: Резюме · Минутки · Разбор ·
/// Стенограмма. Вынесено из MeetingCardView — там и без того больше
/// четырёхсот строк.
///
/// До ревизии карточка отдавала слои кнопками во внешние приложения
/// («Открыть», «Стенограмма», «Исправить стенограмму…» — два последних
/// звали одно и то же), хотя это одна встреча на четырёх глубинах чтения
/// (ревизия 08.08, экран 4; дизайн-аудит 21.08, ход 5). Теперь глубины —
/// сегмент, текст показывается на месте, во внешний редактор ведёт одна
/// кнопка для той глубины, что открыта.
enum MeetingCardDepth: String, CaseIterable, Identifiable {
    case summary
    case minutes
    case analysis
    case transcript

    var id: String { rawValue }

    var title: String {
        switch self {
        case .summary: return L.t("Резюме", "Summary", "摘要")
        case .minutes: return L.t("Минутки", "Minutes", "纪要")
        case .analysis: return L.t("Разбор", "Analysis", "分析")
        case .transcript: return L.t("Стенограмма", "Transcript", "逐字稿")
        }
    }

    /// Какие глубины есть у этой встречи: пустой сегмент — кнопка в никуда.
    /// Минутки — по файлу на диске: card.minutes живёт в кэше репозитория по
    /// равенству снимка и не видит Минутки.md, появившиеся или удалённые при
    /// неизменном статусе (Codex, круг-2 по #391). Ветка без archiveFolder —
    /// на случай карточки, собранной не через MeetingCardLoader (в проде
    /// минуток без папки архива не бывает — только в тестах и превью).
    static func available(card: MeetingCard, meeting: MeetingProcessingSnapshot, conversation: URL? = nil, resolutionComplete: Bool = true) -> [MeetingCardDepth] {
        var out: [MeetingCardDepth] = [.summary]
        if let folder = card.archiveFolder {
            if FileManager.default.fileExists(atPath: folder.appendingPathComponent("Минутки.md").path) {
                out.append(.minutes)
            }
        } else if let minutes = card.minutes, !minutes.isEmpty {
            out.append(.minutes)
        }
        if let analysis = MeetingCardDepth.analysis.file(card: card, meeting: meeting, conversation: conversation, resolutionComplete: resolutionComplete),
           FileManager.default.fileExists(atPath: analysis.path) {
            out.append(.analysis)
        }
        if FileManager.default.fileExists(atPath: meeting.transcriptPath) {
            out.append(.transcript)
        }
        return out
    }

    /// Resolve once off the main actor. The binding is authoritative even when
    /// an archive contains the current transcript beside an older debrief.
    static func conversationFile(card: MeetingCard, meeting: MeetingProcessingSnapshot) -> URL? {
        verifiedConversation(card: card, meeting: meeting)?.url
    }

    static func verifiedConversation(card: MeetingCard, meeting: MeetingProcessingSnapshot) -> (url: URL, text: String)? {
        let fm = FileManager.default
        let transcript = URL(fileURLWithPath: meeting.transcriptPath)
        guard !fm.fileExists(atPath: transcript.path + ".debrief-publication.json"),
              let data = try? Data(contentsOf: URL(fileURLWithPath: transcript.path + ".debrief-binding.json")),
              data.count <= 65536,
              let binding = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              binding["owner"] as? String == transcript.lastPathComponent,
              let publication = binding["publication_id"] as? String, !publication.isEmpty,
              let name = binding["debrief_file"] as? String, !name.isEmpty,
              (name as NSString).lastPathComponent == name, name != ".", name != "..",
              let source = try? Data(contentsOf: transcript, options: .mappedIfSafe) else { return nil }
        let debrief = transcript.deletingLastPathComponent().appendingPathComponent(name)
        guard let body = try? Data(contentsOf: debrief, options: .mappedIfSafe),
              binding["full_sha256"] as? String == digest(source),
              let outputSHA = binding["debrief_sha256"] as? String, outputSHA == digest(body),
              let text = String(data: body, encoding: .utf8) else { return nil }
        if let folder = card.archiveFolder {
            let archived = folder.appendingPathComponent("Разбор.md")
            if let archivedSource = try? Data(contentsOf: folder.appendingPathComponent("Стенограмма.md"), options: .mappedIfSafe),
               archivedSource == source,
               let archivedBody = try? Data(contentsOf: archived, options: .mappedIfSafe),
               digest(archivedBody) == outputSHA {
                return (archived, text)
            }
        }
        return (debrief, text)
    }

    private static func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    /// Файл этой глубины для внешнего редактора: резюме и минутки живут в
    /// архиве встречи; старые карточки без разбора открывают заметку графа.
    func file(card: MeetingCard, meeting: MeetingProcessingSnapshot, conversation: URL? = nil, resolutionComplete: Bool = true) -> URL? {
        let note = meeting.notePath.map { URL(fileURLWithPath: $0) }
        switch self {
        case .summary:
            return card.archiveFolder?.appendingPathComponent("Саммари.md") ?? note
        case .minutes:
            return card.archiveFolder?.appendingPathComponent("Минутки.md") ?? note
        case .analysis:
            return resolutionComplete ? (conversation ?? note) : nil
        case .transcript:
            return URL(fileURLWithPath: meeting.transcriptPath)
        }
    }
}

struct DepthTaskID: Equatable {
    let depth: MeetingCardDepth
    let snapshot: MeetingProcessingSnapshot
    let url: URL?
}

extension MeetingCardView {
    func depth(in available: [MeetingCardDepth]) -> MeetingCardDepth {
        let wanted = MeetingCardDepth(rawValue: depthRaw) ?? .summary
        return available.contains(wanted) ? wanted : .summary
    }

    var availableDepths: [MeetingCardDepth] {
        MeetingCardDepth.available(card: card, meeting: meeting,
                                   conversation: resolvedConversation?.url, resolutionComplete: loadedSnapshot == meeting)
    }

    func depthFile(_ depth: MeetingCardDepth) -> URL? {
        depth.file(card: card, meeting: meeting,
                   conversation: resolvedConversation?.url, resolutionComplete: loadedSnapshot == meeting)
    }

    var depth: MeetingCardDepth { depth(in: availableDepths) }

    /// Сегмент глубин показывается, когда глубин больше одной: у встречи
    /// без минуток и стенограммы выбирать нечего.
    @ViewBuilder
    func depthPicker(available: [MeetingCardDepth], depth: MeetingCardDepth) -> some View {
        if available.count > 1 {
            // Метка черновика — под сегментом, не рядом: четыре русских
            // сегмента плюс метка в одном ряду не влезали в панель 440.
            VStack(alignment: .leading, spacing: 4) {
                Picker("", selection: Binding(
                    get: { depth },
                    set: { depthRaw = $0.rawValue })) {
                    ForEach(available) { Text($0.title).tag($0) }
                }
                .pickerStyle(.segmented).labelsHidden()
                .accessibilityLabel(L.t("Глубина чтения", "Reading depth", "阅读深度"))
                if depth == .minutes, card.minutes?.isDraft == true {
                    Text(L.t("черновик: встреча ещё шла",
                             "draft: the meeting was still running",
                             "草稿：会议仍在进行"))
                        .font(.caption).foregroundStyle(Theme.warning)
                }
            }
        }
    }

    /// Содержимое выбранной глубины.
    @ViewBuilder
    func depthContent(depth: MeetingCardDepth, targetURL: URL?) -> some View {
        switch depth {
        case .summary:
            section(L.t("Решили", "Decided", "决定"), mark: "⚑", items: card.decisions)
            taskSection
            section(L.t("Открытые вопросы", "Open questions", "待解决问题"), mark: "?",
                    items: card.openQuestions)
        case .minutes:
            // Кэш карточки — когда он есть; иначе — файл, прочитанный лениво:
            // чип решает по диску (круг-2 по #391), и панель обязана уметь
            // показать Минутки.md, которых кэш ещё не видел (круг-3, DS+Codex).
            if let minutes = card.minutes, !minutes.isEmpty {
                minutesSections(minutes)
            } else if loadedDepthFile(depth: depth, targetURL: targetURL),
                      let fileMinutes, !fileMinutes.isEmpty {
                minutesSections(fileMinutes)
            } else {
                // Файл не парсится в минутки — показываем его строками, как
                // стенограмму: содержимое честнее пустоты.
                fileView(depth: depth, targetURL: targetURL)
            }
        case .analysis:
            if meeting.state == .error {
                Text(L.t("Обработка не завершена. Сохранённый разбор может относиться к предыдущей версии стенограммы.",
                         "Processing is incomplete. The saved analysis may refer to an earlier transcript version.",
                         "处理尚未完成。已保存的分析可能对应较早的逐字稿版本。"))
                    .font(.caption).foregroundStyle(Theme.warning)
            }
            fileView(depth: depth, targetURL: targetURL)
        case .transcript:
            fileView(depth: depth, targetURL: targetURL)
        }
    }

    private func loadedDepthFile(depth: MeetingCardDepth, targetURL: URL?) -> Bool {
        fileDepth == depth
            && fileSnapshot == meeting
            && fileURL == targetURL
    }

    /// Разбор и стенограмма читаются с диска лениво и показываются на месте.
    /// Стенограмма длинной встречи — сотни абзацев: LazyVStack, не один Text.
    @ViewBuilder
    private func fileView(depth: MeetingCardDepth, targetURL: URL?) -> some View {
        if !loadedDepthFile(depth: depth, targetURL: targetURL) || fileParts == nil {
            HStack(spacing: 7) {
                ProgressView().controlSize(.small)
                Text(L.t("Читаю…", "Reading…", "正在读取…"))
                    .font(.callout).foregroundStyle(.secondary)
            }
        } else if let fileParts, fileParts.isEmpty {
            Text(L.t("Файл пуст.", "The file is empty.", "文件为空。"))
                .font(.callout).foregroundStyle(.secondary)
        } else if let fileParts {
            LazyVStack(alignment: .leading, spacing: 6) {
                ForEach(Array(fileParts.enumerated()), id: \.offset) { _, part in
                    Text(part.displayText)
                        .font(.callout)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
    }

    /// Чтение файла — не на главном потоке: стенограмма двухчасовой
    /// встречи это мегабайты, и карточка не должна замирать при переключении.
    /// Сама функция — на главном акторе: после await она возвращается туда же,
    /// и @State пишется с главного потока (ревью 22.08, локальная голова).
    @MainActor
    func loadDepthFile() async {
        let wanted = depth
        let snapshot = meeting
        let needsMinutesFile = wanted == .minutes
            && (card.minutes?.isEmpty ?? true)
        guard wanted == .analysis || wanted == .transcript || needsMinutesFile,
              let url = depthFile(wanted) else { return }
        // Готовим строки и дословные фрагменты в фоне: рендер получает готовый
        // массив, а не мегабайтную строку на каждый проход тела. Минутки там
        // же и парсим — чтобы панель могла показать файл, которого нет в кэше.
        let verifiedText = wanted == .analysis && url == resolvedConversation?.url ? resolvedConversation?.text : nil
        let (parts, minutes) = await Task.detached(priority: .userInitiated) {
            let text = verifiedText ?? ((try? String(contentsOf: url, encoding: .utf8)) ?? "")
            let parts = MeetingCardFilePart.read(text, verifiedConversation: verifiedText != nil)
            return (parts, needsMinutesFile ? MeetingMinutes.parse(text) : nil)
        }.value
        // .task(id:) отменяет прежнюю задачу при смене глубины или встречи;
        // второй сторож — на случай смены между await и записью.
        guard !Task.isCancelled,
              depth == wanted,
              meeting == snapshot,
              depthFile(wanted) == url else { return }
        fileParts = parts
        fileMinutes = minutes
        fileDepth = wanted
        fileSnapshot = snapshot
        fileURL = url
    }

    /// Прежний ключ meetingCardDetailed (Bool, «Подробно») — в глубину
    /// «Минутки», только если человек его явно выставлял; новая привычка
    /// по умолчанию — Резюме, как в макете.
    static func migrateDepthPreference(defaults: UserDefaults = .standard) {
        guard defaults.object(forKey: "meetingCardDepth") == nil,
              defaults.object(forKey: "meetingCardDetailed") != nil else { return }
        let wanted: MeetingCardDepth = defaults.bool(forKey: "meetingCardDetailed") ? .minutes : .summary
        defaults.set(wanted.rawValue, forKey: "meetingCardDepth")
    }

    /// Откуда это: файл ОТКРЫТОЙ глубины и время его последней записи —
    /// у каждого текста есть источник (правило 3 ревизии). Путь — от папки
    /// графа, иначе от папки данных, иначе последние два звена.
    static func sourceLine(for url: URL, graph: URL?, root: URL, now: Date = Date()) -> String {
        let rel: String
        if let graph, url.path.hasPrefix(graph.path + "/") {
            rel = String(url.path.dropFirst(graph.path.count + 1))
        } else if url.path.hasPrefix(root.path + "/") {
            rel = String(url.path.dropFirst(root.path.count + 1))
        } else {
            rel = url.pathComponents.suffix(2).joined(separator: "/")
        }
        var stamp = ""
        if let date = (try? FileManager.default.attributesOfItem(atPath: url.path))?[.modificationDate] as? Date {
            let f = DateFormatter()
            f.locale = L.locale
            f.setLocalizedDateFormatFromTemplate("d MMM HH:mm")
            stamp = " · " + L.t("обновлено \(f.string(from: date))",
                                 "updated \(f.string(from: date))",
                                 "更新于 \(f.string(from: date))")
        }
        return rel + stamp
    }

    @ViewBuilder
    func minutesSections(_ minutes: MeetingMinutes) -> some View {
        minutesSection(L.t("Темы", "Topics", "议题"), mark: "•", items: minutes.topics)
        minutesSection(L.t("Решили", "Decided", "决定"), mark: "⚑", items: minutes.decisions)
        // Поручения остаются интерактивными: чекбоксы пишут прямо в markdown,
        // и терять их в подробном виде было бы шагом назад.
        taskSection
        minutesSection(L.t("Открытые вопросы", "Open questions", "待解决问题"),
                       mark: "?", items: minutes.openQuestions)
        minutesSection(L.t("Риски", "Risks", "风险"), mark: "!", items: minutes.risks)
    }

    @ViewBuilder
    func minutesSection(_ title: String, mark: String,
                        items: [MeetingMinutes.Item]) -> some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.subheadline.weight(.semibold))
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(item.level == 0 ? mark : "–")
                            .foregroundStyle(item.level == 0 ? Theme.accent : Color.secondary)
                        Text(item.text)
                            .foregroundStyle(item.level == 0 ? .primary : .secondary)
                    }
                    .font(item.level == 0 ? .callout : .caption)
                    .padding(.leading, CGFloat(item.level) * 14)
                }
            }
        }
    }

}

#endif
