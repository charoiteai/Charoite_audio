import SwiftUI

/// Главный экран v1: одна большая кнопка. Всё остальное делает Mac.
struct RecordView: View {
    /// Что показываем поверх экрана. Одно состояние на все листы — иначе
    /// SwiftUI показывает только последний из нескольких `.sheet`.
    private enum Sheet: String, Identifiable {
        case folder, queue, settings
        var id: String { rawValue }
    }

    @StateObject private var rec = Recorder()
    /// Тип записи помнится между запусками: автостарт пишет тем, что
    /// выбирали в прошлый раз, а не всегда «встречу».
    @AppStorage(Recorder.kindStorageKey) private var kindRaw = Recorder.Kind.meeting.rawValue
    /// «Писать сразу при открытии» — по умолчанию включено (№167).
    @AppStorage("record.autostart") private var autostart = true
    /// Первое появление экрана за запуск — единственный момент автостарта.
    @State private var launched = false
    /// Автостарт уже был (или его заменил интент): выбор папки после
    /// подсказки его не повторяет второй раз.
    @State private var autostarted = false
    @State private var sheet: Sheet?
    @State private var queued = 0
    @State private var stuckInQueue = 0

    private var kind: Recorder.Kind { Recorder.Kind(rawValue: kindRaw) ?? .meeting }

    var body: some View {
        VStack(spacing: 24) {
            Picker(L.t("Тип записи", "Recording kind", "录音类型"),
                   selection: Binding(get: { kind }, set: { kindRaw = $0.rawValue })) {
                ForEach(Recorder.Kind.allCases) { k in
                    // Именно title: rawValue — технический идентификатор, он
                    // уходит в имя файла и Live Activity. На экране он давал
                    // «meeting | note | diary» посреди русского интерфейса.
                    Text(k.title).tag(k)
                }
            }
            .pickerStyle(.segmented)
            // Во взводе тип заморожен вместе с ним: переключение на экране
            // не доезжало до пробы, и запись шла старым типом (GLM/DS r1)
            .disabled(rec.isRecording || rec.armed)
            .padding(.horizontal)

            Spacer()

            Button {
                if rec.isRecording {
                    rec.stop()
                } else if rec.armed {
                    rec.disarm()
                } else {
                    rec.start(kind: kind)
                }
            } label: {
                ZStack {
                    Circle()
                        .fill(Theme.record)
                        .frame(width: 132, height: 132)
                        .shadow(color: Theme.accent.opacity(0.45), radius: 18, y: 8)
                    if rec.armed {
                        // Взвод: ждём микрофон у звонка — телефон в руке,
                        // человек должен видеть, что нажимать больше не надо
                        Image(systemName: "phone.arrow.down.left")
                            .font(.system(.title, weight: .semibold))
                            .foregroundStyle(.white)
                    } else {
                        RoundedRectangle(cornerRadius: rec.isRecording ? 10 : 66)
                            .fill(.white)
                            .frame(width: rec.isRecording ? 40 : 44,
                                   height: rec.isRecording ? 40 : 44)
                            .animation(.spring(response: 0.3), value: rec.isRecording)
                    }
                }
            }
            .accessibilityLabel(rec.isRecording
                                ? L.t("Остановить запись", "Stop recording", "停止录音")
                                : rec.armed
                                ? L.t("Отменить ожидание микрофона", "Cancel waiting for the microphone", "取消等待麦克风")
                                : L.t("Начать запись", "Start recording", "开始录音"))

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
                Label(rec.lastResult ?? L.t("Запись остановилась", "Recording stalled", "录音已中断"),
                      systemImage: "exclamationmark.triangle.fill")
                    .font(.callout.weight(.medium))
                    .foregroundStyle(.orange)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 24)
            } else if rec.armed {
                Label(rec.armedStatus ?? L.t("Жду микрофон", "Waiting for the microphone", "等待麦克风"),
                      systemImage: rec.armedBecause == .recorderBusy ? "mic.slash.fill" : "phone.fill")
                    .font(.callout.weight(.medium))
                    .foregroundStyle(Theme.accent)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 24)
            } else {
                Text(rec.lastResult ?? L.t(
                    "Стоп — и запись уедет на Mac через iCloud.\nДальше он сам: стенограмма, минутки, граф.",
                    "Stop, and the recording travels to the Mac over iCloud.\nThe rest is on it: transcript, minutes, graph.",
                    "按停止，录音便经 iCloud 送往 Mac。\n之后由它接手：逐字稿、纪要、图谱。"))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 28)
            }

            // Автостарт ждёт папку доставки — сказать об этом здесь, а не
            // молчать оранжевым лотком в тулбаре (критика DS r2)
            if !rec.isRecording, !rec.armed, autostart, !Inbox.folderChosen {
                Text(L.t("Писать сразу при открытии — после выбора папки доставки (лоток вверху)",
                         "Recording on open starts once the delivery folder is chosen (tray above)",
                         "选定投递文件夹后（上方托盘）即可打开即录"))
                    .font(.footnote)
                    .foregroundStyle(.orange)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 28)
            }

            // Запись можно забрать руками — не дожидаясь iCloud и не завися от
            // него вовсе. 03.08 файл был единственным экземпляром получаса
            // встречи, и достать его из приложения было нечем.
            if !rec.isRecording, let last = rec.lastRecording {
                ShareLink(item: last) {
                    Label(L.t("Поделиться записью", "Share recording", "分享录音")
                          + " · \(Inbox.sizeText(last))",
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
                          ? L.t("В очереди \(queued) · \(stuckInQueue) ждёт дольше суток",
                                "\(queued) queued · \(stuckInQueue) waiting over a day",
                                "队列中 \(queued) 个 · \(stuckInQueue) 个已等待超过一天")
                          : L.t("В очереди записей: \(queued)",
                                "Recordings queued: \(queued)",
                                "排队中的录音：\(queued)"),
                          systemImage: stuckInQueue > 0
                          ? "exclamationmark.triangle.fill" : "tray.full")
                        .font(.footnote)
                        .foregroundStyle(stuckInQueue > 0 ? .orange : Theme.accent)
                }
                .padding(.bottom, 6)
            }
        }
        .padding(.vertical)
        .navigationTitle(L.t("Запись", "Record", "录音"))
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button {
                    sheet = .settings
                } label: {
                    Image(systemName: "gearshape")
                }
                .accessibilityLabel(L.t("Настройки записи", "Recording settings", "录音设置"))
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    sheet = .folder
                } label: {
                    // Лоток «наверх», не папка: у вкладки встреч своя папка
                    // (граф), и две одинаковые иконки folder.* на соседних
                    // экранах спрашивали у человека одно и то же разными
                    // папками. Невыбранная — оранжевая: записи не уедут.
                    Image(systemName: Inbox.folderChosen
                          ? "tray.and.arrow.up.fill" : "tray.and.arrow.up")
                        .foregroundStyle(Inbox.folderChosen ? Theme.accent : .orange)
                }
                .accessibilityLabel(L.t("Папка доставки записей", "Delivery folder", "投递文件夹"))
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
                        rec.lastResult = L.t("Папка выбрана: \(url.lastPathComponent)",
                                             "Folder set: \(url.lastPathComponent)",
                                             "已选择文件夹：\(url.lastPathComponent)")
                        // Подсказка обещала «после выбора папки» — держим слово
                        // в этой же сессии, один раз (критика GLM r3)
                        if !autostarted, Recorder.shouldAutoStart(enabled: autostart && !Self.underTests,
                                                                  coldLaunch: true,
                                                                  isRecording: rec.isRecording, armed: rec.armed,
                                                                  deliveryReady: true) {
                            autostarted = true
                            rec.start(kind: kind)
                        }
                        Task { await Inbox.flush { msg in rec.lastResult = msg } }
                    } catch {
                        rec.lastResult = L.t("Не удалось запомнить папку: \(error.localizedDescription)",
                                             "Could not remember the folder: \(error.localizedDescription)",
                                             "无法记住该文件夹：\(error.localizedDescription)")
                    }
                }
            case .queue:
                QueueView(rec: rec)
            case .settings:
                RecordSettingsView()
            }
        }
        .task {
            // Кнопка «Стоп» в Live Activity выполняется в процессе приложения
            // (LiveActivityIntent), но про сам рекордер она ничего не знает —
            // виджету он недоступен и не должен быть. Здесь и связываем.
            RecordingControl.onStop = { [weak rec] in rec?.stop() }
            // «Начать запись» из Siri/Команд/кнопки действия — тем же типом,
            // что выбран на экране.
            RecordingControl.onStart = { [weak rec] in
                guard let rec, !rec.isRecording else { return }
                let raw = UserDefaults.standard.string(forKey: Recorder.kindStorageKey) ?? ""
                rec.start(kind: Recorder.Kind(rawValue: raw) ?? .meeting)
            }
            if !launched {
                launched = true
                // Слушать сразу (№167): просьба интента важнее настройки;
                // без просьбы — автостарт по настройке, один раз за запуск,
                // только когда папка доставки выбрана (первый запуск без
                // папки писал бы в никуда) и не под XCTest — хост тестов
                // иначе начинал настоящую запись (GLM r1).
                if RecordingControl.takeStartRequest() {
                    autostarted = true
                    rec.start(kind: kind)
                } else if Recorder.shouldAutoStart(enabled: autostart && !Self.underTests,
                                                   coldLaunch: true,
                                                   isRecording: rec.isRecording, armed: rec.armed,
                                                   deliveryReady: Inbox.folderChosen) {
                    autostarted = true
                    rec.start(kind: kind)
                }
            }
            await Inbox.flush { msg in rec.lastResult = msg }
            rec.refreshLastRecording()
            refreshQueue()
        }
        .onChange(of: rec.lastRecording) { _, _ in refreshQueue() }
    }

    private static var underTests: Bool {
        ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil
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
