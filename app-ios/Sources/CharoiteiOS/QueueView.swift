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
                        L.t("Очередь пуста", "Queue is empty", "队列为空"),
                        systemImage: "checkmark.circle",
                        description: Text(L.t("Всё, что записано, уехало на Mac.",
                                              "Everything recorded has reached the Mac.",
                                              "所有录音都已送达 Mac。")))
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
                            Text(L.t("Ждут отправки: \(items.count)",
                                     "Waiting to be sent: \(items.count)",
                                     "等待发送：\(items.count)"))
                        }
                    }
                }
            }
            .navigationTitle(L.t("Очередь", "Queue", "队列"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button(L.t("Готово", "Done", "完成")) { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        send()
                    } label: {
                        if sending {
                            ProgressView()
                        } else {
                            Text(L.t("Отправить", "Send", "发送"))
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
                        rec.lastResult = L.t("Не удалось запомнить папку: \(error.localizedDescription)",
                                             "Could not remember the folder: \(error.localizedDescription)",
                                             "无法记住该文件夹：\(error.localizedDescription)")
                    }
                }
            }
        }
        .task { reload() }
    }

    private var folderWarning: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label(L.t("Папка доставки не выбрана", "No delivery folder chosen", "尚未选择投递文件夹"),
                  systemImage: "tray.and.arrow.up")
                .font(.callout.weight(.medium))
            Text(L.t(
                "Записи никуда не уедут, пока не укажете папку доставки в iCloud Drive — ту же, за которой следит Mac (обычно «Charoite Inbox»). Папка графа с вкладки «Встречи» — другая, она про чтение.",
                "Recordings stay put until you pick a delivery folder in iCloud Drive — the same one the Mac watches (usually «Charoite Inbox»). The graph folder on the Meetings tab is a different one: that is for reading.",
                "在你于 iCloud Drive 中指定投递文件夹之前，录音哪儿也不会去——就是 Mac 所监视的那个（通常是「Charoite Inbox」）。「会议」标签页里的图谱文件夹是另一个，它用于读取。"))
                .font(.footnote)
                .foregroundStyle(.secondary)
            Button(L.t("Выбрать папку доставки", "Choose delivery folder", "选择投递文件夹")) { showPicker = true }
                .buttonStyle(.borderedProminent)
                .tint(Theme.accent)
        }
        .padding(.vertical, 4)
    }

    private var stuckWarning: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(L.t("Записи ждут дольше суток", "Recordings waiting over a day", "录音已等待超过一天"),
                  systemImage: "exclamationmark.triangle.fill")
                .font(.callout.weight(.medium))
                .foregroundStyle(.orange)
            // Сутки — не про терпение, а про диагноз: обычная доставка занимает
            // секунды, и всё, что висит дольше, висит уже не «сейчас уедет».
            Text(L.t(
                "Обычно доставка занимает секунды. Проверьте, что на iPhone есть сеть и место в iCloud, — или заберите записи вручную кнопкой «Поделиться».",
                "Delivery normally takes seconds. Check that the iPhone has a network and free iCloud space — or take the recordings by hand with «Share».",
                "投递通常只需几秒。请检查 iPhone 是否有网络、iCloud 是否还有空间——或用「分享」手动取走录音。"))
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
