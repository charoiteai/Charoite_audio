import SwiftUI

#if os(macOS)

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
    /// неизменном статусе (Codex, круг-2 по #391); распарсенные минутки без
    /// archiveFolder остаются запасным признаком.
    static func available(card: MeetingCard, meeting: MeetingProcessingSnapshot) -> [MeetingCardDepth] {
        var out: [MeetingCardDepth] = [.summary]
        if let folder = card.archiveFolder {
            if FileManager.default.fileExists(atPath: folder.appendingPathComponent("Минутки.md").path) {
                out.append(.minutes)
            }
        } else if let minutes = card.minutes, !minutes.isEmpty {
            out.append(.minutes)
        }
        if let note = meeting.notePath, FileManager.default.fileExists(atPath: note) {
            out.append(.analysis)
        }
        if FileManager.default.fileExists(atPath: meeting.transcriptPath) {
            out.append(.transcript)
        }
        return out
    }

    /// Файл этой глубины для внешнего редактора: резюме и минутки живут в
    /// архиве встречи, разбор — заметка графа, стенограмма — она сама.
    func file(card: MeetingCard, meeting: MeetingProcessingSnapshot) -> URL? {
        let note = meeting.notePath.map { URL(fileURLWithPath: $0) }
        switch self {
        case .summary:
            return card.archiveFolder?.appendingPathComponent("Саммари.md") ?? note
        case .minutes:
            return card.archiveFolder?.appendingPathComponent("Минутки.md") ?? note
        case .analysis:
            return note
        case .transcript:
            return URL(fileURLWithPath: meeting.transcriptPath)
        }
    }
}

extension MeetingCardView {
    var depth: MeetingCardDepth {
        let wanted = MeetingCardDepth(rawValue: depthRaw) ?? .summary
        let available = MeetingCardDepth.available(card: card, meeting: meeting)
        return available.contains(wanted) ? wanted : .summary
    }

    /// Сегмент глубин показывается, когда глубин больше одной: у встречи
    /// без минуток и стенограммы выбирать нечего.
    @ViewBuilder
    var depthPicker: some View {
        let available = MeetingCardDepth.available(card: card, meeting: meeting)
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
    var depthContent: some View {
        switch depth {
        case .summary:
            section(L.t("Решили", "Decided", "决定"), mark: "⚑", items: card.decisions)
            taskSection
            section(L.t("Открытые вопросы", "Open questions", "待解决问题"), mark: "?",
                    items: card.openQuestions)
        case .minutes:
            if let minutes = card.minutes { minutesSections(minutes) }
        case .analysis, .transcript:
            fileView
        }
    }

    /// Разбор и стенограмма читаются с диска лениво и показываются на месте.
    /// Стенограмма длинной встречи — сотни абзацев: LazyVStack, не один Text.
    @ViewBuilder
    private var fileView: some View {
        if fileDepth != depth || fileMeetingID != meeting.meetingID || fileLines == nil {
            HStack(spacing: 7) {
                ProgressView().controlSize(.small)
                Text(L.t("Читаю…", "Reading…", "正在读取…"))
                    .font(.callout).foregroundStyle(.secondary)
            }
        } else if let fileLines, fileLines.isEmpty {
            Text(L.t("Файл пуст.", "The file is empty.", "文件为空。"))
                .font(.callout).foregroundStyle(.secondary)
        } else if let fileLines {
            LazyVStack(alignment: .leading, spacing: 6) {
                ForEach(Array(fileLines.enumerated()), id: \.offset) { _, line in
                    Text(MarkdownLine.render(line))
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
        let meetingID = meeting.meetingID
        guard wanted == .analysis || wanted == .transcript,
              let url = wanted.file(card: card, meeting: meeting) else { return }
        // Режем на непустые строки в фоне один раз: рендер получает готовый
        // массив, а не мегабайтную строку на каждый проход тела.
        let lines = await Task.detached(priority: .userInitiated) {
            ((try? String(contentsOf: url, encoding: .utf8)) ?? "")
                .components(separatedBy: "\n")
                .filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
        }.value
        // .task(id:) отменяет прежнюю задачу при смене глубины или встречи;
        // второй сторож — на случай смены между await и записью.
        guard !Task.isCancelled, depth == wanted, meeting.meetingID == meetingID else { return }
        fileLines = lines
        fileDepth = wanted
        fileMeetingID = meetingID
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
    var sourceLine: String? {
        guard let url = depth.file(card: card, meeting: meeting) else { return nil }
        return Self.sourceLine(for: url, graph: AppSettings.graphDir, root: AppSettings.charoiteRoot)
    }

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
