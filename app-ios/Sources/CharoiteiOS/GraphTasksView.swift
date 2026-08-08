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
                    Label(L.t("Сначала папка графа", "Pick the graph folder first", "请先选择图谱文件夹"),
                          systemImage: "checklist")
                } description: {
                    Text(L.t("Укажите папку вашего графа в Файлах (iCloud Drive → Obsidian) — задачи придут из тех же файлов, что и встречи.",
                             "Point the app at your graph folder in Files (iCloud Drive → Obsidian) — tasks come from the same files as meetings.",
                             "在「文件」中指向你的图谱文件夹（iCloud Drive → Obsidian）——任务与会议来自同一批文件。"))
                } actions: {
                    Button(L.t("Выбрать папку графа", "Choose graph folder", "选择图谱文件夹")) { showPicker = true }
                        .buttonStyle(.borderedProminent)
                        .tint(Theme.accent)
                }
            } else if store.tasks.isEmpty {
                ContentUnavailableView(
                    L.t("Задач нет", "No tasks", "暂无任务"),
                    systemImage: "checklist",
                    description: Text(L.t(
                        "Поручения из минуток появятся здесь после ближайшей встречи.",
                        "Action items from the minutes will show up here after your next meeting.",
                        "会议纪要中的行动项会在下一场会议后出现在这里。")))
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
        .navigationTitle(store.openCount > 0
                         ? L.t("Задачи", "Tasks", "任务") + " · \(store.openCount)"
                         : L.t("Задачи", "Tasks", "任务"))
        .sheet(isPresented: $showPicker) {
            FolderPicker { url in
                do {
                    try store.saveFolder(url)
                    store.rescanTasks()
                } catch {
                    store.status = L.t("Не удалось запомнить папку: \(error.localizedDescription)",
                                       "Could not remember the folder: \(error.localizedDescription)",
                                       "无法记住该文件夹：\(error.localizedDescription)")
                }
            }
        }
        .task { store.rescanTasks() }
    }
}

#Preview { NavigationStack { GraphTasksView() } }
