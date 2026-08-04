import AVFoundation
import SwiftUI

#if os(macOS)

/// Экран первого запуска.
///
/// Раньше человек, открыв программу впервые, видел пустой суфлёр: две серые
/// панели и кнопка «Слушать встречу». Ни что это, ни зачем ему микрофон, ни
/// куда уедет запись — нигде не сказано. Для программы, которая просит доступ
/// к звуку рабочих совещаний, молчание в этом месте дороже любой другой
/// экономии: человек либо не нажмёт, либо нажмёт, не понимая, на что согласился.
struct FirstRunView: View {
    /// Показываем один раз; отметка переживает перезапуск.
    @AppStorage("charoit.firstRunSeen") private var seen = false
    @Environment(\.dismiss) private var dismiss
    @ObservedObject private var readiness = SetupReadinessService.shared
    @ObservedObject private var pulls = ModelPullService.shared
    @State private var requestingMicrophone = false
    let onStart: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            header
            Divider()
            VStack(alignment: .leading, spacing: 12) {
                step("waveform.circle.fill",
                     L.t("Слушает встречу", "Listens to the meeting", "旁听会议"),
                     L.t("Берёт звук из микрофона и, если настроен BlackHole, из звонка. Бот не входит во встречу; сообщите участникам о записи.",
                         "Takes audio from the microphone and, when BlackHole is configured, from the call. No bot joins; tell participants about the recording.",
                         "采集麦克风声音，并在配置 BlackHole 后采集通话声音。不会有机器人加入；请告知参与者正在录音。"))
                step("lock.laptopcomputer",
                     L.t("Всё остаётся на этом Mac", "Everything stays on this Mac", "一切都留在这台 Mac 上"),
                     L.t("Распознавание речи и разбор идут локально. Записи, стенограммы и тезисы никуда не отправляются.",
                         "Speech recognition and analysis run locally. Recordings, transcripts and theses are never sent anywhere.",
                         "语音识别与分析均在本地进行。录音、逐字稿与要点不会发送到任何地方。"))
                step("list.bullet.rectangle",
                     L.t("После встречи — сам напишет", "Writes it up afterwards", "会后自动整理"),
                     L.t("Стенограмма, тезисы, минутки и решения складываются в папку встреч. Потом можно спросить: «что обсуждали вчера?»",
                         "Transcript, theses, minutes and decisions land in the meetings folder. Later you can ask: \"what did we discuss yesterday?\"",
                         "逐字稿、要点、纪要与决定都会存入会议文件夹。之后你可以问：「昨天讨论了什么？」"))
            }
            readinessPanel
            Spacer(minLength: 0)
            footer
        }
        .padding(28)
        .frame(width: 560, height: 680)
        .onAppear {
            warmUpFolderAccess()
            readiness.refresh()
        }
    }

    /// Спрашиваем доступ к папке заранее, пока человек читает этот экран.
    ///
    /// Граф встреч живёт в iCloud, и первое обращение к нему поднимает
    /// системный запрос «приложение хочет получить доступ к файлам iCloud
    /// Drive». Раньше он прилетал в момент, когда демон дописывал граф, — то
    /// есть посреди встречи, поверх разговора. Безобидное чтение каталога
    /// здесь переносит вопрос в спокойную минуту.
    private func warmUpFolderAccess() {
        DispatchQueue.global(qos: .utility).async {
            // Греем ФАКТИЧЕСКУЮ папку графа, а не жёсткий путь Obsidian:
            // у пользователя с локальным vault прогрев по чужому пути ничего
            // не открывал, и системный запрос всё равно прилетал посреди
            // встречи — ровно тогда, когда его тут и пытаются предотвратить.
            let target = AppSettings.graphDir ?? FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Mobile Documents/iCloud~md~obsidian/Documents")
            _ = try? FileManager.default.contentsOfDirectory(atPath: target.path)
        }
    }

    private var header: some View {
        HStack(spacing: 14) {
            ZStack {
                Circle()
                    .fill(Theme.brand)
                    .frame(width: 46, height: 46)
                Text(L.t("Ч", "C", "C"))   // литера логотипа
                    .font(.system(.title2, design: .rounded, weight: .bold))
                    .foregroundStyle(.white)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(L.t("Чароит", "Charoite", "Charoite")).font(.system(.title2, weight: .bold))
                Text(L.t("Суфлёр рабочих встреч", "Meeting copilot", "会议助手")).foregroundStyle(.secondary)
            }
        }
    }

    private func step(_ icon: String, _ title: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 17))
                .foregroundStyle(Theme.accent)
                .frame(width: 24)
                .accessibilityHidden(true)      // смысл несёт текст рядом
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.system(size: 13, weight: .semibold))
                Text(text)
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .combine)
    }

    private var readinessPanel: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack {
                Text(L.t("Готовность к первой встрече", "Ready for the first meeting", "首次会议准备情况"))
                    .font(.system(size: 13, weight: .semibold))
                Spacer()
                if readiness.isChecking {
                    ProgressView().controlSize(.small)
                } else {
                    Button(L.t("Проверить снова", "Check again", "再次检查")) {
                        readiness.refresh()
                    }
                    .buttonStyle(.link)
                    .font(.caption)
                }
            }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    ForEach(readiness.snapshot?.checks ?? []) { check in
                        readinessRow(check)
                    }
                    if readiness.snapshot == nil, !readiness.isChecking {
                        Text(L.t("Не удалось выполнить проверку",
                                 "The readiness check could not run",
                                 "无法执行准备情况检查"))
                            .font(.caption)
                            .foregroundStyle(.red)
                    }
                }
            }
            .frame(height: 205)
        }
        .padding(12)
        .background(.quaternary.opacity(0.55), in: RoundedRectangle(cornerRadius: 10))
    }

    private func readinessRow(_ check: SetupCheck) -> some View {
        HStack(alignment: .top, spacing: 9) {
            Image(systemName: check.state == .ready
                  ? "checkmark.circle.fill"
                  : check.state == .warning ? "exclamationmark.triangle.fill" : "xmark.circle.fill")
                .foregroundStyle(readinessColor(check.state))
                .frame(width: 16)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 1) {
                Text(check.title).font(.system(size: 12, weight: .medium))
                Text(check.detail)
                    .font(.system(size: 10.5))
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                fixActions(check)
            }
        }
        .accessibilityElement(children: .combine)
    }

    /// Починить, не открывая терминал: рецепт из detail исполняется кнопкой.
    ///
    /// «Первая стенограмма без терминала» ломалась ровно здесь: проверка уже
    /// сказала, чего не хватает, человек уже согласен — а дальше «наберите
    /// команду». Модели качает само приложение через API Ollama; для того,
    /// что кнопкой не чинится, команда хотя бы копируется, а не
    /// перепечатывается с экрана.
    @ViewBuilder
    private func fixActions(_ check: SetupCheck) -> some View {
        if check.state != .ready {
            let models = SetupReadinessPolicy.pullableModels(in: check.detail)
            if !models.isEmpty {
                HStack(spacing: 10) {
                    ForEach(models, id: \.self) { model in
                        if let status = pulls.progress[model] {
                            HStack(spacing: 4) {
                                ProgressView().controlSize(.mini)
                                Text("\(model): \(status)").font(.system(size: 10.5))
                                    .foregroundStyle(.secondary)
                            }
                        } else {
                            Button(L.t("Скачать \(model)", "Pull \(model)", "拉取 \(model)")) {
                                pulls.pull(model)
                            }
                            .buttonStyle(.link)
                            .font(.system(size: 11))
                        }
                    }
                }
                if let err = models.compactMap({ pulls.failed[$0] }).first {
                    Text(err).font(.system(size: 10)).foregroundStyle(.red)
                }
            } else if let cmd = SetupReadinessPolicy.copyableCommand(in: check.detail) {
                Button(L.t("Скопировать команду", "Copy command", "复制命令")) {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(cmd, forType: .string)
                }
                .buttonStyle(.link)
                .font(.system(size: 11))
            }
        }
    }

    private func readinessColor(_ state: SetupCheck.State) -> Color {
        switch state {
        case .ready: return .green
        case .warning: return .orange
        case .blocked: return .red
        }
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Про микрофон предупреждаем ДО нажатия: системный запрос,
            // прилетевший посреди встречи, — худший момент из возможных.
            Label(L.t("При первом запуске macOS спросит доступ к микрофону — без него слушать нечего.",
                      "On first run macOS will ask for microphone access — without it there is nothing to listen to.",
                      "首次运行时 macOS 会请求麦克风权限——没有它就无从旁听。"),
                  systemImage: "info.circle")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
            // новичку без встреч есть что пощупать: демо-граф в комплекте
            Label(L.t("Нет встреч? В комплекте демо-граф (папка demo/) — наведи на него graph_dir и спроси «что решили по платёжному провайдеру?»",
                      "No meetings yet? A demo graph ships in demo/ — point graph_dir at it and ask \"what did we decide about the payment provider?\"",
                      "还没有会议？随附示例图谱（demo/ 文件夹）——把 graph_dir 指向它，然后问「支付服务商的事定了什么？」"),
                  systemImage: "sparkles")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)

            HStack {
                Button(L.t("Осмотрюсь сам", "I'll look around", "我自己看看")) {
                    seen = true
                    dismiss()
                }
                SettingsLink {
                    Label(L.t("Настройки", "Settings", "设置"), systemImage: "gearshape")
                }
                Spacer()
                Button {
                    beginFirstMeeting()
                } label: {
                    Text(L.t("Начать слушать встречу", "Start listening", "开始旁听"))
                        .fontWeight(.medium)
                        .padding(.horizontal, 6)
                }
                .keyboardShortcut(.defaultAction)
                .disabled(readiness.isChecking || requestingMicrophone
                          || readiness.snapshot?.canStart != true)
            }
        }
    }

    /// Явное нажатие «Начать» — правильный момент для системного вопроса.
    /// При отказе мастер остаётся на экране и превращает предупреждение в
    /// точную ошибку; отметку «онбординг пройден» раньше разрешения не ставим.
    private func beginFirstMeeting() {
        guard readiness.snapshot?.canStart == true else { return }
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            finishFirstMeetingStart()
        case .notDetermined:
            requestingMicrophone = true
            AVCaptureDevice.requestAccess(for: .audio) { granted in
                Task { @MainActor in
                    requestingMicrophone = false
                    if granted {
                        finishFirstMeetingStart()
                    } else {
                        readiness.refresh()
                    }
                }
            }
        default:
            readiness.refresh()
        }
    }

    private func finishFirstMeetingStart() {
        seen = true
        dismiss()
        onStart()
    }
}

#endif
