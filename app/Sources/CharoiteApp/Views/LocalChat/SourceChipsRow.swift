import SwiftUI

#if os(macOS)

/// Чипы источников под ответом памяти: встречи — заливкой индиго, узлы и
/// досье — фиолетовым контуром. Клик по встрече ведёт на её карточку в
/// библиотеке; узлы, досье и документы открываются файлом.
struct SourceChipsRow: View {
    let sources: [MemoryScreenPolicy.Source]

    var body: some View {
        // FlowLayout в проекте нет; чипов не больше шести и они короткие —
        // перенос даёт обычный HStack в две строки через chunk.
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                HStack(spacing: 4) {
                    ForEach(row) { source in
                        chip(source)
                    }
                }
            }
        }
        .padding(.top, 2)
    }

    private var rows: [[MemoryScreenPolicy.Source]] {
        stride(from: 0, to: sources.count, by: 3).map {
            Array(sources[$0..<min($0 + 3, sources.count)])
        }
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
    }

    private func open(_ source: MemoryScreenPolicy.Source) {
        if source.kind == .meeting {
            let stem = (source.rel as NSString).lastPathComponent
                .replacingOccurrences(of: ".md", with: "")
            // Карточка в библиотеке, если запись ещё в истории конвейера;
            // id записей бывают с секундами — совпадение по префиксу минуты.
            if let record = MeetingRepository.shared.records.first(where: {
                $0.id == stem || $0.id.hasPrefix(stem) || stem.hasPrefix($0.id)
            }) {
                WorkspaceNavigation.shared.open(.meetings, meetingID: record.id)
                return
            }
        }
        if let graph = AppSettings.graphDir {
            NSWorkspace.shared.open(graph.appendingPathComponent(source.rel))
        }
    }
}

#endif
