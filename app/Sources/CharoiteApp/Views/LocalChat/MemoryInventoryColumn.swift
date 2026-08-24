import SwiftUI

#if os(macOS)

/// Правая колонка «Что знает память» (макет MOBILE_2026-08, экран «Память»):
/// счёт встреч/узлов/досье и свежие ядра со строкой состояния. Данные —
/// GraphInventoryService, колонка сама диск не трогает.
struct MemoryInventoryColumn: View {
    let snapshot: GraphInventoryService.Snapshot

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            PaneHeader(L.t("Что знает память", "What memory knows", "记忆掌握的内容"),
                       systemImage: "brain.head.profile")
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    counters
                    if !snapshot.cores.isEmpty {
                        cores
                    }
                }
                .padding(12)
            }
        }
            }

    private var counters: some View {
        // Числа с источником: это счёт файлов графа, не самочувствие UI.
        HStack(spacing: 6) {
            counter(snapshot.meetings,
                    LibraryScreenPolicy.meetings(snapshot.meetings))
            counter(snapshot.nodes,
                    MemoryScreenPolicy.nodesLabel(snapshot.nodes))
            counter(snapshot.dossiers,
                    MemoryScreenPolicy.dossiersLabel(snapshot.dossiers))
        }
    }

    private func counter(_ n: Int, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("\(n)")
                .font(.title3.weight(.semibold)).monospacedDigit()
            // подпись без числа: само число уже сверху
            Text(label.drop(while: { $0.isNumber || $0 == " " }))
                .font(.caption2).foregroundStyle(.secondary)
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
            .fill(Color(nsColor: .controlBackgroundColor)))
    }

    private var cores: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(L.t("Ядра — сквозные темы", "Cores — cross-meeting topics", "核心——跨会议主题").uppercased())
                .font(.caption2.weight(.semibold)).kerning(0.8)
                .foregroundStyle(.secondary)
            ForEach(snapshot.cores) { core in
                Button {
                    if let graph = AppSettings.graphDir {
                        NSWorkspace.shared.open(
                            graph.appendingPathComponent("Ядра/\(core.name).md"))
                    }
                } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(core.name).font(.caption.weight(.medium)).lineLimit(1)
                        HStack(spacing: 4) {
                            if !core.status.isEmpty {
                                Text(core.status).lineLimit(1)
                            }
                            Text("·").foregroundStyle(.quaternary)
                            Text(Self.dayFormatter.string(from: core.updated)).monospacedDigit()
                        }
                        .font(.caption2).foregroundStyle(.secondary)
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
                }
                .buttonStyle(.plain)
                .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                    .fill(Color(nsColor: .controlBackgroundColor)))
                .help(L.t("Открыть ядро", "Open the core", "打开核心"))
            }
        }
    }

    private static let dayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = L.locale
        f.setLocalizedDateFormatFromTemplate("d.MM")
        return f
    }()
}

#endif
