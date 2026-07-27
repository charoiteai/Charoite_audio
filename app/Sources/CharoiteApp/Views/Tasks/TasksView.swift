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
                    .foregroundStyle(Theme.accent)
                Text("Задачи со встреч").font(.headline).fixedSize()
                Text("\(tasks.openCount) открытых")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Toggle(isOn: $showDone) { Text("Показывать сделанные").fixedSize() }
                    .toggleStyle(.checkbox)
                    .font(.caption)
                // все открытые — в буфер: вставить список в письмо/чат команды
                Button {
                    let open = tasks.items.filter { !$0.done }
                        .map { "- [ ] \($0.text)" }.joined(separator: "\n")
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(open, forType: .string)
                } label: {
                    Image(systemName: "doc.on.doc")
                }
                .help("Скопировать все открытые задачи списком")
                .disabled(tasks.openCount == 0)
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
                // секции по файлам: десяток задач плоским списком читался
                // кашей — теперь видно, с какой встречи хвосты
                List {
                    ForEach(groups, id: \.rel) { group in
                        Section {
                            ForEach(group.items) { item in
                                row(item)
                            }
                        } header: {
                            Text(group.rel.replacingOccurrences(of: ".md", with: ""))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
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

    /// Файлы с их задачами, свежие файлы сверху (порядок visible сохранён).
    private var groups: [(rel: String, items: [TasksService.Item])] {
        var order: [String] = []
        var byRel: [String: [TasksService.Item]] = [:]
        for item in visible {
            if byRel[item.rel] == nil { order.append(item.rel) }
            byRel[item.rel, default: []].append(item)
        }
        return order.map { (rel: $0, items: byRel[$0] ?? []) }
    }

    private func row(_ item: TasksService.Item) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Button {
                tasks.toggle(item)
            } label: {
                Image(systemName: item.done ? "checkmark.square.fill" : "square")
                    .foregroundStyle(item.done ? Color.accentColor : .secondary)
            }
            .buttonStyle(.plain)
            Text(MarkdownLine.render(item.text))
                .strikethrough(item.done)
                .foregroundStyle(item.done ? .secondary : .primary)
            Spacer()
        }
        .padding(.vertical, 2)
    }
}

#endif
