import SwiftUI

/// Главный экран v1: одна большая кнопка. Всё остальное делает Mac.
struct RecordView: View {
    /// Что показываем поверх экрана. Одно состояние на все листы — иначе
    /// SwiftUI показывает только последний из нескольких `.sheet`.
    private enum Sheet: String, Identifiable {
        case folder, queue
        var id: String { rawValue }
    }

    @StateObject private var rec = Recorder()
    @State private var kind: Recorder.Kind = .meeting
    @State private var sheet: Sheet?
    @State private var queued = 0
    @State private var stuckInQueue = 0

    var body: some View {
        VStack(spacing: 24) {
            Picker("Тип записи", selection: $kind) {
                ForEach(Recorder.Kind.allCases) { k in
                    // Именно title: rawValue — технический идентификатор, он
                    // уходит в имя файла и Live Activity. На экране он давал
                    // «meeting | note | diary» посреди русского интерфейса.
                    Text(k.title).tag(k)
                }
            }
            .pickerStyle(.segmented)
            .disabled(rec.isRecording)
            .padding(.horizontal)

            Spacer()

            Button {
                rec.isRecording ? rec.stop() : rec.start(kind: kind)
            } label: {
                ZStack {
                    Circle()
                        .fill(Theme.record)
                        .frame(width: 132, height: 132)
                        .shadow(color: Theme.accent.opacity(0.45), radius: 18, y: 8)
                    RoundedRectangle(cornerRadius: rec.isRecording ? 10 : 66)
                        .fill(.white)
                        .frame(width: rec.isRecording ? 40 : 44,
                               height: rec.isRecording ? 40 : 44)
                        .animation(.spring(response: 0.3), value: rec.isRecording)
                }
            }
            .accessibilityLabel(rec.isRecording ? "Остановить запись" : "Начать запись")

            Text(timeString(rec.elapsed))
                .font(.system(size: 34, weight: .thin, design: .default))
                .monospacedDigit()
                .opacity(rec.isRecording ? 1 : 0.35)

            LevelWave(level: rec.level)
                .frame(height: 28)
                .opacity(rec.isRecording ? 1 : 0.2)

            Spacer()

            // Тревога о вставшей записи — не мелким серым в общей строке:
            // именно её человек должен увидеть, не вглядываясь в таймер.
            if rec.stalled {
                Label(rec.lastResult ?? "Запись остановилась", systemImage: "exclamationmark.triangle.fill")
                    .font(.callout.weight(.medium))
                    .foregroundStyle(.orange)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 24)
            } else {
                Text(rec.lastResult ?? "Стоп — и запись уедет на Mac через iCloud.\nДальше он сам: стенограмма, минутки, граф.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 28)
            }

            // Запись можно забрать руками — не дожидаясь iCloud и не завися от
            // него вовсе. 03.08 файл был единственным экземпляром получаса
            // встречи, и достать его из приложения было нечем.
            if !rec.isRecording, let last = rec.lastRecording {
                ShareLink(item: last) {
                    Label("Поделиться записью · \(Inbox.sizeText(last))",
                          systemImage: "square.and.arrow.up")
                        .font(.footnote)
                }
                .padding(.bottom, 4)
            }

            // Очередь — не серая строка, а вход. За числом «в очереди: 6»
            // может стоять получасовая встреча недельной давности, про которую
            // человек уверен, что она давно на Mac.
            if !rec.isRecording, queued > 0 {
                Button {
                    sheet = .queue
                } label: {
                    Label(stuckInQueue > 0
                          ? "В очереди \(queued) · \(stuckInQueue) ждёт дольше суток"
                          : "В очереди записей: \(queued)",
                          systemImage: stuckInQueue > 0
                          ? "exclamationmark.triangle.fill" : "tray.full")
                        .font(.footnote)
                        .foregroundStyle(stuckInQueue > 0 ? .orange : Theme.accent)
                }
                .padding(.bottom, 6)
            }
        }
        .padding(.vertical)
        .navigationTitle("Запись")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    sheet = .folder
                } label: {
                    Image(systemName: Inbox.folderChosen ? "folder.badge.gearshape" : "folder.badge.questionmark")
                }
                .accessibilityLabel("Папка доставки")
            }
        }
        // Один sheet на все листы: два `.sheet` на одном элементе SwiftUI не
        // складывает — показывается только последний, и кнопка очереди молча
        // ничего не делала.
        .sheet(item: $sheet, onDismiss: refreshQueue) { which in
            switch which {
            case .folder:
                FolderPicker { url in
                    do {
                        try Inbox.saveFolder(url)
                        rec.lastResult = "Папка выбрана: \(url.lastPathComponent)"
                        Task { await Inbox.flush { msg in rec.lastResult = msg } }
                    } catch {
                        rec.lastResult = "Не удалось запомнить папку: \(error.localizedDescription)"
                    }
                }
            case .queue:
                QueueView(rec: rec)
            }
        }
        .task {
            await Inbox.flush { msg in rec.lastResult = msg }
            rec.refreshLastRecording()
            refreshQueue()
        }
        .onChange(of: rec.lastRecording) { _, _ in refreshQueue() }
    }

    /// Пересчитать очередь: SwiftUI за файловой системой не следит, а число в
    /// строке должно меняться сразу после стопа и после досылки.
    private func refreshQueue() {
        let items = Inbox.queuedItems
        queued = items.count
        stuckInQueue = items.filter { $0.isStuck() }.count
    }

    private func timeString(_ t: TimeInterval) -> String {
        let s = Int(t)
        return String(format: "%02d:%02d", s / 60, s % 60)
    }
}

/// Простая живая волна уровня — без буфера истории, честные текущие 12 столбиков.
struct LevelWave: View {
    var level: Float
    var body: some View {
        HStack(alignment: .center, spacing: 4) {
            ForEach(0..<12, id: \.self) { i in
                Capsule()
                    .fill(Theme.accent.opacity(0.8))
                    .frame(width: 4,
                           height: 6 + CGFloat(level) * CGFloat(6 + (i * 7) % 22))
            }
        }
        .animation(.linear(duration: 0.18), value: level)
    }
}

#Preview { NavigationStack { RecordView() } }
