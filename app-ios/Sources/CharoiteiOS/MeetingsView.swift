import SwiftUI

/// Лента встреч из графа: что Mac собрал — телефон показывает.
struct MeetingsView: View {
    @ObservedObject var store = GraphStore.shared
    @State private var showPicker = false

    var body: some View {
        Group {
            if !store.folderChosen {
                emptyGraph
            } else if store.meetings.isEmpty {
                ContentUnavailableView(
                    L.t("Пока пусто", "Nothing yet", "暂时为空"),
                    systemImage: "calendar",
                    description: Text(store.status ?? L.t(
                        "Встречи появятся после первой записи — Mac собирает граф сам.",
                        "Meetings show up after your first recording — the Mac builds the graph itself.",
                        "第一次录音之后会议就会出现——图谱由 Mac 自行构建。")))
            } else {
                List {
                    if let s = store.status {
                        Text(s).font(.footnote).foregroundStyle(.secondary)
                    }
                    ForEach(store.meetings) { m in
                        NavigationLink(value: m.id) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(m.title).font(.body).lineLimit(2)
                                Text(m.stamp).font(.caption).foregroundStyle(.secondary)
                            }
                            .padding(.vertical, 2)
                        }
                    }
                }
                .navigationDestination(for: String.self) { id in
                    if let m = store.meetings.first(where: { $0.id == id }) {
                        MeetingDetail(meeting: m)
                    }
                }
                .refreshable { store.rescanMeetings() }
            }
        }
        .navigationTitle(L.t("Встречи", "Meetings", "会议"))
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { showPicker = true } label: {
                    // Книги, не папка: на вкладке записи папкой называется
                    // доставка, и одинаковые folder.*-иконки на двух экранах
                    // просили «выбрать папку», не говоря какую.
                    Image(systemName: store.folderChosen
                          ? "books.vertical.fill" : "books.vertical")
                        .foregroundStyle(store.folderChosen ? Theme.accent : .orange)
                }
                .accessibilityLabel(L.t("Папка графа встреч", "Meetings graph folder", "会议图谱文件夹"))
            }
        }
        .sheet(isPresented: $showPicker) {
            FolderPicker { url in
                do {
                    try store.saveFolder(url)
                    store.rescanMeetings()
                } catch {
                    store.status = L.t("Не удалось запомнить папку: \(error.localizedDescription)",
                                       "Could not remember the folder: \(error.localizedDescription)",
                                       "无法记住该文件夹：\(error.localizedDescription)")
                }
            }
        }
        .task { store.rescanMeetings() }
    }

    private var emptyGraph: some View {
        ContentUnavailableView {
            Label(L.t("Выберите папку графа", "Choose the graph folder", "选择图谱文件夹"),
                  systemImage: "books.vertical")
        } description: {
            Text(L.t(
                "Один раз укажите папку вашего графа в Файлах (iCloud Drive → Obsidian) — лента встреч и задачи будут читаться прямо из неё. Это не папка доставки записей: та настраивается на вкладке «Запись».",
                "Point the app at your graph folder in Files once (iCloud Drive → Obsidian) — the meetings feed and tasks are read straight from it. This is not the delivery folder: that one lives on the Record tab.",
                "只需在「文件」中指定一次你的图谱文件夹（iCloud Drive → Obsidian）——会议动态与任务将直接从中读取。这不是录音投递文件夹：那个在「录音」标签页设置。"))
        } actions: {
            Button(L.t("Выбрать папку графа", "Choose graph folder", "选择图谱文件夹")) { showPicker = true }
                .buttonStyle(.borderedProminent)
                .tint(Theme.accent)
        }
    }
}

/// Просмотр встречи: markdown как текст — быстро и честно; ссылки и
/// оформление живут в Obsidian, телефону важно содержание.
struct MeetingDetail: View {
    let meeting: GraphStore.Meeting
    @State private var text = ""

    var body: some View {
        ScrollView {
            if let manifest = meeting.manifest {
                VStack(alignment: .leading, spacing: 14) {
                    if !manifest.participants.isEmpty {
                        Text(L.t("Участники: ", "Participants: ", "参会者：")
                             + manifest.participants.joined(separator: ", "))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if let summary = manifest.summary { Text(summary).font(.body) }
                    cardSection(L.t("Решили", "Decided", "决定"), manifest.decisions)
                    cardSection(L.t("Поручения", "Action items", "任务"), manifest.actionItems)
                    cardSection(L.t("Открытые вопросы", "Open questions", "待解决问题"),
                                manifest.openQuestions)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(16)
            } else {
                Text(text)
                    .font(.callout)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(16)
            }
        }
        .navigationTitle(meeting.title)
        .navigationBarTitleDisplayMode(.inline)
        .task { if meeting.manifest == nil { text = GraphStore.shared.text(of: meeting) } }
    }

    @ViewBuilder
    private func cardSection(_ title: String, _ items: [String]) -> some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 5) {
                Text(title).font(.headline)
                ForEach(items, id: \.self) { Text("• \($0)").font(.callout) }
            }
        }
    }
}

#Preview { NavigationStack { MeetingsView() } }
