import SwiftUI

#if os(macOS)

/// Чипы источников под ответом памяти: встречи — заливкой индиго, узлы и
/// досье — фиолетовым контуром. Клик по встрече ведёт на её карточку в
/// библиотеке; узлы, досье и документы открываются файлом.
struct SourceChipsRow: View {
    let sources: [MemoryScreenPolicy.Source]

    var body: some View {
        SourceChipFlow {
            ForEach(sources) { source in
                chip(source)
            }
        }
        .padding(.top, 2)
    }

    private func chip(_ source: MemoryScreenPolicy.Source) -> some View {
        Button {
            open(source)
        } label: {
            Text(source.title)
                .font(.caption2.weight(.semibold))
                .lineLimit(1)
                .foregroundStyle(Theme.accent)
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background {
                    if source.kind == .meeting {
                        Capsule().fill(Theme.accent.opacity(0.13))
                    } else {
                        Capsule().strokeBorder(Theme.accent.opacity(0.35), lineWidth: 1)
                    }
                }
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .help(source.rel)
        .accessibilityLabel(Text(source.title))
        .accessibilityHint(L.t("Открыть материал источника", "Open source material", "打开来源资料"))
    }

    private func open(_ source: MemoryScreenPolicy.Source) {
        if source.kind == .meeting {
            let stem = (source.rel as NSString).lastPathComponent
                .replacingOccurrences(of: ".md", with: "")
            // Карточка в библиотеке, если запись ещё в истории конвейера.
            // Матчинг — в политике: точный штамп, затем префикс на границе
            // сегмента (голый префикс путал пары «одной минуты», #388).
            let ids = MeetingRepository.shared.records.map(\.id)
            if let id = MemoryScreenPolicy.matchRecord(stem: stem, ids: ids) {
                WorkspaceNavigation.shared.open(.meetings, meetingID: id)
                return
            }
        }
        if let graph = AppSettings.graphDir {
            NSWorkspace.shared.open(graph.appendingPathComponent(source.rel))
        }
    }
}

#endif
