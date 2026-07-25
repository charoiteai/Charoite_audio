import SwiftUI

#if os(macOS)

/// Задачи со встреч: все `- [ ]` графа одним списком, клик — отметить в файле.
struct TasksView: View {
    @ObservedObject private var tasks = TasksService.shared
    @State private var showDone = false

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: "checklist")
                    .foregroundStyle(Color(hex: "#6366F1"))
                Text("Задачи со встреч").font(.headline).fixedSize()
                Text("\(tasks.openCount) открытых")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Toggle(isOn: $showDone) { Text("Показывать сделанные").fixedSize() }
                    .toggleStyle(.checkbox)
                    .font(.caption)
                Button {
                    tasks.rescan()
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .help("Перечитать граф")
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            Divider()
            if visible.isEmpty {
                VStack(spacing: 10) {
                    Image(systemName: "checkmark.circle")
                        .font(.largeTitle).foregroundStyle(.quaternary)
                    Text(tasks.openCount == 0 && !tasks.items.isEmpty
                         ? "Всё сделано"
                         : "Поручения из минуток появятся здесь.\nМинутки пишут их чекбоксами — как в Obsidian.")
                        .font(.subheadline).foregroundStyle(.tertiary)
                        .multilineTextAlignment(.center)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(visible) { item in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Button {
                            tasks.toggle(item)
                        } label: {
                            Image(systemName: item.done ? "checkmark.square.fill" : "square")
                                .foregroundStyle(item.done ? Color.accentColor : .secondary)
                        }
                        .buttonStyle(.plain)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(MarkdownLine.render(item.text))
                                .strikethrough(item.done)
                                .foregroundStyle(item.done ? .secondary : .primary)
                            Text(item.rel.replacingOccurrences(of: ".md", with: ""))
                                .font(.caption2).foregroundStyle(.tertiary)
                        }
                        Spacer()
                    }
                    .padding(.vertical, 2)
                }
                .listStyle(.inset)
            }
        }
        .frame(minWidth: 420, minHeight: 320)
        .onAppear { tasks.rescan() }
    }

    private var visible: [TasksService.Item] {
        showDone ? tasks.items : tasks.items.filter { !$0.done }
    }
}

#endif
