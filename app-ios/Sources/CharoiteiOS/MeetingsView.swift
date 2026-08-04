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
                    "Пока пусто",
                    systemImage: "calendar",
                    description: Text(store.status ?? "Встречи появятся после первой записи — Mac собирает граф сам."))
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
        .navigationTitle("Встречи")
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
                .accessibilityLabel("Папка графа встреч")
            }
        }
        .sheet(isPresented: $showPicker) {
            FolderPicker { url in
                do {
                    try store.saveFolder(url)
                    store.rescanMeetings()
                } catch {
                    store.status = "Не удалось запомнить папку: \(error.localizedDescription)"
                }
            }
        }
        .task { store.rescanMeetings() }
    }

    private var emptyGraph: some View {
        ContentUnavailableView {
            Label("Выберите папку графа", systemImage: "books.vertical")
        } description: {
            Text("Один раз укажите папку вашего графа в Файлах (iCloud Drive → Obsidian) — лента встреч и задачи будут читаться прямо из неё. Это не папка доставки записей: та настраивается на вкладке «Запись».")
        } actions: {
            Button("Выбрать папку графа") { showPicker = true }
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
            Text(text)
                .font(.callout)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(16)
        }
        .navigationTitle(meeting.stamp)
        .navigationBarTitleDisplayMode(.inline)
        .task { text = GraphStore.shared.text(of: meeting) }
    }
}

#Preview { NavigationStack { MeetingsView() } }
