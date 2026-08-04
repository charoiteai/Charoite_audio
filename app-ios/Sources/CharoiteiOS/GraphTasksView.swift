import SwiftUI

/// Задачи со встреч: те же чекбоксы `- [ ]`, что видят Mac и Obsidian.
/// Отметка пишется прямо в markdown-файл графа — один источник истины.
struct GraphTasksView: View {
    @ObservedObject var store = GraphStore.shared
    @State private var showPicker = false

    var body: some View {
        Group {
            if !store.folderChosen {
                // Кнопка прямо здесь, а не отсылка на соседнюю вкладку: папка
                // у задач и встреч одна, и гонять человека за ней в другое
                // место — лишний шаг ровно в момент первого запуска.
                ContentUnavailableView {
                    Label("Сначала папка графа", systemImage: "checklist")
                } description: {
                    Text("Укажите папку вашего графа в Файлах (iCloud Drive → Obsidian) — задачи придут из тех же файлов, что и встречи.")
                } actions: {
                    Button("Выбрать папку графа") { showPicker = true }
                        .buttonStyle(.borderedProminent)
                        .tint(Theme.accent)
                }
            } else if store.tasks.isEmpty {
                ContentUnavailableView(
                    "Задач нет",
                    systemImage: "checklist",
                    description: Text("Поручения из минуток появятся здесь после ближайшей встречи."))
            } else {
                List(store.tasks) { t in
                    Button {
                        store.toggle(t)
                    } label: {
                        HStack(alignment: .top, spacing: 10) {
                            Image(systemName: t.done ? "checkmark.circle.fill" : "circle")
                                .foregroundStyle(t.done ? Theme.ok : .secondary)
                                .font(.title3)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(t.text)
                                    .strikethrough(t.done)
                                    .foregroundStyle(t.done ? .secondary : .primary)
                                Text(t.rel)
                                    .font(.caption2)
                                    .foregroundStyle(.tertiary)
                                    .lineLimit(1)
                            }
                        }
                        .padding(.vertical, 2)
                    }
                    .buttonStyle(.plain)
                }
                .refreshable { store.rescanTasks() }
            }
        }
        .navigationTitle(store.openCount > 0 ? "Задачи · \(store.openCount)" : "Задачи")
        .sheet(isPresented: $showPicker) {
            FolderPicker { url in
                do {
                    try store.saveFolder(url)
                    store.rescanTasks()
                } catch {
                    store.status = "Не удалось запомнить папку: \(error.localizedDescription)"
                }
            }
        }
        .task { store.rescanTasks() }
    }
}

#Preview { NavigationStack { GraphTasksView() } }
