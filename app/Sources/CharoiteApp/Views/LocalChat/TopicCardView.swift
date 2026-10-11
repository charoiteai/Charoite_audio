import SwiftUI

#if os(macOS)
struct TopicCardView: View {
    let graph: URL
    let folder: String
    let name: String
    @Environment(\.dismiss) private var dismiss
    @State private var snapshot: TopicCard.Snapshot?
    @State private var failed = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(name).font(.title2.weight(.semibold))
                Spacer()
                Button(L.t("Закрыть", "Close", "关闭")) { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            if let snapshot {
                provenance(snapshot)
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        ForEach(TopicCard.Section.allCases, id: \.self) { section in
                            sectionView(section, snapshot: snapshot)
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
                Button(L.t("Открыть исходный документ", "Open source document", "打开源文档")) {
                    open(snapshot.document)
                }
            } else if failed {
                Text(L.t("Не удалось прочитать тему. Проверьте файл и повторите открытие.",
                         "Could not read the topic. Check the file and reopen the card.",
                         "无法读取主题。请检查文件并重新打开卡片。"))
            } else {
                ProgressView()
            }
        }
        .padding(20)
        .frame(width: 680, height: 620)
        .task {
            let root = graph, coreFolder = folder, topic = name
            let loaded = await Task.detached(priority: .utility) {
                try? TopicCard.load(graph: root, folder: coreFolder, name: topic)
            }.value
            guard !Task.isCancelled else { return }
            snapshot = loaded
            failed = loaded == nil
        }
    }

    private func provenance(_ snapshot: TopicCard.Snapshot) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(snapshot.isDossier
                 ? L.t("Сводка досье; источники не перепроверялись при открытии.",
                       "Dossier summary; sources were not rechecked when opening.",
                       "档案摘要；打开时未重新核查来源。")
                 : L.t("Досье недоступно — показаны разделы ядра.",
                       "Dossier unavailable — showing core sections.", "档案不可用，显示核心章节。"))
            if let date = snapshot.assembled, !date.isEmpty {
                Text(L.t("Сводка собрана: ", "Summary assembled: ", "摘要生成于：") + date)
            } else if let date = snapshot.modified {
                Text(L.t("Документ изменён: ", "Document modified: ", "文档修改于：") + date.formatted())
            }
        }.font(.caption).foregroundStyle(.secondary)
    }

    private func sectionView(_ section: TopicCard.Section, snapshot: TopicCard.Snapshot) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title(section)).font(.headline)
            let body = snapshot.sections[section] ?? ""
            if body.isEmpty || body == "—" || body == "-" {
                Text(L.t("Нет сведений в документе", "No information in the document", "文档中无相关信息"))
                    .foregroundStyle(.secondary)
            } else {
                // Literal text avoids opening model-authored external Markdown URLs.
                Text(body).textSelection(.enabled)
                ForEach(snapshot.sources[section] ?? []) { source in
                    Button(source.target) { if let url = source.url { open(url) } }
                        .buttonStyle(.link)
                        .disabled(source.url == nil)
                        .help(source.url == nil
                              ? L.t("Источник не найден или имя неоднозначно", "Source missing or ambiguous", "来源缺失或名称不明确")
                              : source.target)
                }
            }
        }
    }

    private func open(_ url: URL) {
        // Recheck at click time: a sync operation can replace a source with a symlink.
        if TopicCard.isInside(url, graph: graph) { NSWorkspace.shared.open(url) }
    }

    private func title(_ section: TopicCard.Section) -> String {
        switch section {
        case .current: return L.t("Состояние по сводке", "State in the summary", "摘要中的状态")
        case .changes: return L.t("Как менялась тема", "How the topic changed", "主题变化历史")
        case .decisions: return L.t("Записанные решения", "Recorded decisions", "记录的决定")
        case .open: return L.t("Открытые вопросы и поручения", "Open questions and actions", "待解决问题与任务")
        case .uncertainty: return L.t("Записанные неясности", "Recorded uncertainties", "记录的不确定事项")
        case .people: return L.t("Кто в теме", "People involved", "相关人员")
        case .author: return L.t("Правки автора", "Author edits", "作者修改")
        }
    }
}
#endif
