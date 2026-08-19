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
    @ObservedObject private var runtime = OllamaRuntimeService.shared
    @State private var requestingMicrophone = false
    let onStart: () -> Void

    /// Два обязательных поля конфига — прямо в мастере.
    ///
    /// Раньше человек обязан был открыть config/config.yaml в редакторе и
    /// вписать имя и путь к графу. Для «поставил и работает» это лишний шаг,
    /// причём первый же: без имени микрофон в стенограмме подписан обезличенно.
    @State private var ownerName = AppSettings.configValue("user_name") ?? ""
    @State private var graphPath = AppSettings.configValue("graph_dir") ?? ""
    @State private var configSaved = false
    @State private var presetSaveFailed = false
    /// Текст отказа записи конфига; nil — отказа не было.
    @State private var configSaveFailure: String?
    /// Выбранный набор моделей. По умолчанию — тот, что уже в конфиге, а
    /// если конфиг ещё не тронут, рекомендованный под память этой машины.
    @State private var presetID: String = ModelPresetPolicy.current(
        model: AppSettings.configValue("model"),
        smallModel: AppSettings.configValue("small_model")
    )?.id ?? ModelPresetPolicy.recommended(forGB: ModelPresetPolicy.machineMemoryGB).id

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            header
            Divider()
            // Середина прокручивается: окно мастера фиксированной высоты, а
            // блоков в нём прибавляется (настройка, готовность, установка
            // моделей). Обрезанный блок — это молча потерянный шаг установки,
            // ровно та ошибка, что была с переполненным тулбаром встречи.
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    stepsBlock
                    configPanel
                    modelsPanel
                    readinessPanel
                }
            }
            footer
        }
        .padding(28)
        // 860, а не 720: с блоком выбора моделей нижняя кнопка уходила под
        // край — человек её не видел и не прокручивал, потому что не знал,
        // что там что-то есть. Экран 13" (1117 pt) держит 860 с запасом.
        .frame(width: 580, height: 860)
        .onAppear {
            warmUpFolderAccess()
            readiness.refresh(force: true)
            // Состояние движка спрашиваем отдельно: проверка готовности видит
            // только «порт не отвечает», а человеку нужно знать, установлен
            // ли он вообще и что нажать.
            Task { await runtime.refresh() }
        }
    }

    private var stepsBlock: some View {
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

    /// Набор моделей под память ЭТОЙ машины.
    ///
    /// Раньше пресеты лежали комментарием в config.example.yaml, и человек
    /// сам узнавал объём своей памяти и переносил строчки. Ошибка была
    /// молчаливой: тяжёлая модель не падает, она уходит в своп — и вывод
    /// делается «продукт медленный», а не «модель не по машине».
    private var modelsPanel: some View {
        let memory = ModelPresetPolicy.machineMemoryGB
        let advised = ModelPresetPolicy.recommended(forGB: memory)
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                Text(L.t("Модели под вашу машину", "Models for your Mac", "适配本机的模型"))
                    .font(.system(size: 13, weight: .semibold))
                Text(L.t("\(memory) ГБ памяти", "\(memory) GB of memory", "\(memory) GB 内存"))
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            }
            ForEach(ModelPresetPolicy.all) { preset in
                Button { presetID = preset.id } label: {
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: presetID == preset.id
                              ? "largecircle.fill.circle" : "circle")
                            .foregroundStyle(presetID == preset.id ? Theme.accent : .secondary)
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(spacing: 6) {
                                Text(preset.title).font(.system(size: 12, weight: .medium))
                                if preset.id == advised.id {
                                    Text(L.t("рекомендуем", "recommended", "推荐"))
                                        .font(.system(size: 10, weight: .medium))
                                        .padding(.horizontal, 5).padding(.vertical, 1)
                                        .background(Capsule().fill(Theme.accent.opacity(0.14)))
                                        .foregroundStyle(Theme.accent)
                                }
                                // Честно говорим, если вариант тяжелее машины:
                                // спрятать его значило бы решить за человека,
                                // а он может знать, что делает.
                                if preset.needsGB > memory {
                                    Text(L.t("нужно ~\(preset.needsGB) ГБ",
                                             "needs ~\(preset.needsGB) GB",
                                             "需要约 \(preset.needsGB) GB"))
                                        .font(.system(size: 10))
                                        .foregroundStyle(Theme.overdue)
                                }
                            }
                            Text(preset.note)
                                .font(.system(size: 10.5)).foregroundStyle(.secondary)
                            Text(preset.models.joined(separator: " · "))
                                .font(.system(size: 10, design: .monospaced))
                                .foregroundStyle(.tertiary)
                        }
                        Spacer(minLength: 0)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
            HStack(spacing: 8) {
                Button(L.t("Записать в конфиг и скачать", "Save and download", "保存并下载")) {
                    applyPreset()
                }
                .charoite(.regular, .m)
                .disabled(pulls.isPulling(selectedPreset.model)
                          || pulls.isPulling(selectedPreset.smallModel))
                if presetSaveFailed {
                    Text(L.t("Не удалось записать пресет в config.yaml — проверьте путь установки",
                             "Could not write the preset to config.yaml — check the install path",
                             "无法将预设写入 config.yaml——请检查安装路径"))
                        .font(.caption)
                        .foregroundStyle(.red)
                }
                ForEach(selectedPreset.models, id: \.self) { model in
                    if let status = pulls.progress[model] {
                        HStack(spacing: 4) {
                            ProgressView().controlSize(.small)
                            Text("\(model): \(status)")
                                .font(.system(size: 10.5)).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            if let err = selectedPreset.models.compactMap({ pulls.failed[$0] }).first {
                Text(err).font(.system(size: 10)).foregroundStyle(.red).lineLimit(2)
            }
        }
    }

    private var selectedPreset: ModelPreset {
        ModelPresetPolicy.preset(id: presetID) ?? ModelPresetPolicy.all[1]
    }

    /// Записать выбор в конфиг и поставить недостающие модели.
    private func applyPreset() {
        let preset = selectedPreset
        // результат записи не игнорируется (аудит 14.08): пресет, тихо не
        // доехавший до config.yaml, оставлял модели по умолчанию без слова
        // Профиль — это не только две модели: на лёгком наборе он ещё и
        // выключает граф знаний и дежавю. Раньше об этом была фраза в
        // описании, а конвейер всё равно звал разбор — и каждая встреча
        // кончалась «ошибкой обработки» на сломанном JSON.
        var saved = AppSettings.setConfigValue("model", preset.model)
            && AppSettings.setConfigValue("small_model", preset.smallModel)
        for flag in preset.configFlags where saved {
            saved = AppSettings.setConfigValue(flag.key, flag.value)
        }
        if !saved {
            presetSaveFailed = true
            return
        }
        presetSaveFailed = false
        for model in preset.models { pulls.pull(model) }
        readiness.refresh(force: true)
    }

    /// Имя и папка графа — то, что раньше правили в YAML руками.
    private var configPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L.t("Как к вам обращаться и где хранить знания",
                     "Your name and where to keep the knowledge",
                     "您的称呼与知识存放位置"))
                .font(.system(size: 13, weight: .semibold))
            HStack(spacing: 8) {
                TextField(L.t("Ваше имя", "Your name", "您的姓名"), text: $ownerName)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 190)
                    .onSubmit(saveConfig)
                TextField(L.t("Папка графа знаний", "Knowledge graph folder", "知识图谱文件夹"),
                          text: $graphPath)
                    .textFieldStyle(.roundedBorder)
                Button(L.t("Выбрать…", "Choose…", "选择…")) { chooseGraphFolder() }
                    .charoite(.regular, .m)
            }
            HStack(spacing: 8) {
                Button(L.t("Сохранить", "Save", "保存")) { saveConfig() }
                    .charoite(.prominent, .m)
                    .disabled(ownerName.trimmingCharacters(in: .whitespaces).isEmpty)
                if configSaved {
                    Text(L.t("Сохранено в config.yaml", "Saved to config.yaml", "已保存到 config.yaml"))
                        .font(.system(size: 11)).foregroundStyle(.secondary)
                        .transition(.opacity)
                }
                // Отказ записи виден человеку. Раньше «Сохранено» показывалось
                // безусловно: на свежей установке конфиг не создавался, готовность
                // оставалась красной, и понять причину было неоткуда (аудит P0-7).
                if let failure = configSaveFailure {
                    Label {
                        Text(failure)
                            .font(.system(size: 11))
                    } icon: {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                    }
                    .transition(.opacity)
                }
            }
            Text(L.t("Имя — метка вашего микрофона в стенограмме. Папка пустая = граф выключен, работает только расшифровка.",
                     "The name labels your microphone in the transcript. An empty folder means the graph is off and only transcription runs.",
                     "姓名用于在逐字稿中标记您的麦克风。留空文件夹表示关闭图谱，仅进行转写。"))
                .font(.system(size: 10.5)).foregroundStyle(.secondary)

            // Разделение голосов: единственный шаг установки, ради которого
            // раньше приходилось открывать терминал уже после того, как
            // приложение заработало.
            if !ModelPullService.diarizationInstalled {
                HStack(spacing: 8) {
                    if let status = pulls.progress[ModelPullService.diarizationKey] {
                        ProgressView().controlSize(.small)
                        Text(status).font(.system(size: 11)).foregroundStyle(.secondary)
                    } else {
                        Button(L.t("Различать голоса собеседников",
                                   "Tell speakers apart",
                                   "区分不同说话人")) { pulls.pullDiarization() }
                            .charoite(.regular, .m)
                        Text(L.t("модель ~80 МБ, ставится один раз",
                                 "~80 MB model, installed once",
                                 "约 80 MB 模型，仅需安装一次"))
                            .font(.system(size: 10.5)).foregroundStyle(.secondary)
                    }
                }
                if let err = pulls.failed[ModelPullService.diarizationKey] {
                    Text(err).font(.system(size: 10)).foregroundStyle(.red).lineLimit(2)
                }
            }
        }
    }

    private func chooseGraphFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = L.t("Выбрать", "Choose", "选择")
        if panel.runModal() == .OK, let url = panel.url {
            graphPath = url.path
            saveConfig()
        }
    }

    private func saveConfig() {
        let name = ownerName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        // Результат записи проверяем: раньше «Сохранено» показывалось даже
        // тогда, когда config.yaml не создавался вовсе, и новый пользователь
        // упирался в красную готовность без единой подсказки (аудит P0-7).
        let ok = AppSettings.setConfigValue("user_name", name)
            && AppSettings.setConfigValue("graph_dir",
                                          graphPath.trimmingCharacters(in: .whitespaces))
        withAnimation {
            configSaved = ok
            configSaveFailure = ok ? nil : Self.saveFailureHint()
        }
        // Проверка готовности читает конфиг — пусть увидит новые значения.
        readiness.refresh(force: true)
    }

    /// Что именно сказать человеку, когда запись не удалась. Причина всегда
    /// одна из двух: папка данных недоступна для записи или в поставке не
    /// нашёлся образец конфига — и обе чинятся по-разному.
    private static func saveFailureHint() -> String {
        let root = AppSettings.charoiteRoot.path
        if AppSettings.configExampleURL == nil {
            return L.t("Не найден образец config.example.yaml в поставке — переустановите приложение",
                       "config.example.yaml is missing from the bundle — reinstall the app",
                       "安装包中缺少 config.example.yaml — 请重新安装应用")
        }
        return L.t("Не удалось записать config.yaml в \(root) — проверьте доступ к папке",
                   "Could not write config.yaml to \(root) — check folder permissions",
                   "无法将 config.yaml 写入 \(root) — 请检查文件夹权限")
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
                        readiness.refresh(force: true)
                    }
                    .charoite(.quiet, .s)
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
            // Движок моделей — единственное, что раньше уводило в терминал:
            // «запустите Ollama» человек читал, уже перетащив приложение в
            // «Программы», и дальше шёл искать команды. Теперь это кнопка.
            if check.id == "ollama", models.isEmpty {
                HStack(spacing: 8) {
                    if let busy = runtime.busy {
                        ProgressView().controlSize(.mini)
                        Text(busy).font(.system(size: 10.5)).foregroundStyle(.secondary)
                    } else {
                        let title = OllamaRuntimeService.actionTitle(for: runtime.state)
                        if !title.isEmpty {
                            Button(title) { Task { await runtime.fix() } }
                                .charoite(.regular, .s)
                        }
                        Text(OllamaRuntimeService.explanation(for: runtime.state))
                            .font(.system(size: 10.5)).foregroundStyle(.secondary)
                    }
                }
                if let err = runtime.failure {
                    Text(err).font(.system(size: 10)).foregroundStyle(.red)
                }
            } else if !models.isEmpty {
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
                            .charoite(.regular, .s)
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
                .charoite(.regular, .s)
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
                .charoite(.quiet, .l)
                SettingsLink {
                    Label(L.t("Настройки", "Settings", "设置"), systemImage: "gearshape")
                }
                .charoite(.regular, .l)
                Spacer()
                Button {
                    beginFirstMeeting()
                } label: {
                    Text(L.t("Начать слушать встречу", "Start listening", "开始旁听"))
                }
                .charoite(.prominent, .l)
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
                        readiness.refresh(force: true)
                    }
                }
            }
        default:
            readiness.refresh(force: true)
        }
    }

    private func finishFirstMeetingStart() {
        seen = true
        dismiss()
        onStart()
    }
}

#endif
