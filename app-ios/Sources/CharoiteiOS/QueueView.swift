import SwiftUI

/// Очередь недоставленных записей.
///
/// Раньше про неё говорила одна серая строка «в очереди: 6». За таким числом
/// может стоять что угодно: шесть свежих файлов, которые уедут через минуту,
/// или получасовая встреча недельной давности, про которую человек уверен, что
/// она давно на Mac. 03.08 запись потерялась именно так — молча, и заметили это
/// только когда понадобилась стенограмма.
///
/// Здесь очередь видно целиком: что записано, когда, сколько весит, сколько
/// ждёт. Каждую запись можно отдать руками, не дожидаясь iCloud.
struct QueueView: View {
    @ObservedObject var rec: Recorder
    @Environment(\.dismiss) private var dismiss
    @State private var items: [Inbox.Item] = []
    @State private var sending = false
    @State private var showPicker = false

    var body: some View {
        NavigationStack {
            Group {
                if items.isEmpty {
                    ContentUnavailableView(
                        "Очередь пуста",
                        systemImage: "checkmark.circle",
                        description: Text("Всё, что записано, уехало на Mac."))
                } else {
                    List {
                        if !Inbox.folderChosen {
                            Section {
                                folderWarning
                            }
                        } else if items.contains(where: { $0.isStuck() }) {
                            Section {
                                stuckWarning
                            }
                        }
                        Section {
                            ForEach(items) { item in
                                row(item)
                            }
                        } header: {
                            Text("Ждут отправки: \(items.count)")
                        }
                    }
                }
            }
            .navigationTitle("Очередь")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Готово") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        send()
                    } label: {
                        if sending {
                            ProgressView()
                        } else {
                            Text("Отправить")
                        }
                    }
                    .disabled(items.isEmpty || sending)
                }
            }
            .sheet(isPresented: $showPicker) {
                FolderPicker { url in
                    do {
                        try Inbox.saveFolder(url)
                        send()
                    } catch {
                        rec.lastResult = "Не удалось запомнить папку: \(error.localizedDescription)"
                    }
                }
            }
        }
        .task { reload() }
    }

    private var folderWarning: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Папка доставки не выбрана", systemImage: "folder.badge.questionmark")
                .font(.callout.weight(.medium))
            Text("Записи никуда не уедут, пока не укажете папку в iCloud Drive — ту же, за которой следит Mac.")
                .font(.footnote)
                .foregroundStyle(.secondary)
            Button("Выбрать папку") { showPicker = true }
                .buttonStyle(.borderedProminent)
                .tint(Theme.accent)
        }
        .padding(.vertical, 4)
    }

    private var stuckWarning: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Записи ждут дольше суток", systemImage: "exclamationmark.triangle.fill")
                .font(.callout.weight(.medium))
                .foregroundStyle(.orange)
            // Сутки — не про терпение, а про диагноз: обычная доставка занимает
            // секунды, и всё, что висит дольше, висит уже не «сейчас уедет».
            Text("Обычно доставка занимает секунды. Проверьте, что на iPhone есть сеть и место в iCloud, — или заберите записи вручную кнопкой «Поделиться».")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
    }

    private func row(_ item: Inbox.Item) -> some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(item.name)
                    .font(.body)
                Text("\(when(item.recorded)) · \(item.size)")
                    .font(.caption)
                    .foregroundStyle(item.isStuck() ? .orange : .secondary)
            }
            Spacer(minLength: 8)
            ShareLink(item: item.url) {
                Image(systemName: "square.and.arrow.up")
            }
            .buttonStyle(.plain)
            .foregroundStyle(Theme.accent)
        }
        .padding(.vertical, 2)
    }

    /// «сегодня 13:32», «вчера 18:05», «28 июля».
    private func when(_ date: Date) -> String {
        let cal = Calendar.current
        let time = DateFormatter()
        // Локаль приложения, а не устройства: под русским заголовком «Заметка»
        // системный форматтер писал «July 28».
        time.locale = L.locale
        if cal.isDateInToday(date) {
            time.dateFormat = "HH:mm"
            return L.t("сегодня", "today", "今天") + " " + time.string(from: date)
        }
        if cal.isDateInYesterday(date) {
            time.dateFormat = "HH:mm"
            return L.t("вчера", "yesterday", "昨天") + " " + time.string(from: date)
        }
        time.setLocalizedDateFormatFromTemplate("d MMMM")
        return time.string(from: date)
    }

    private func reload() {
        items = Inbox.queuedItems
    }

    private func send() {
        sending = true
        Task {
            await Inbox.flush { msg in rec.lastResult = msg }
            rec.refreshLastRecording()
            reload()
            sending = false
        }
    }
}

#Preview { QueueView(rec: Recorder()) }
