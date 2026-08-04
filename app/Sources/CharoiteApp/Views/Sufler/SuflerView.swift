import AppKit
import SwiftUI

#if os(macOS)

/// Суфлёр: левая панель — стенограмма в реальном времени, правая — тезисы и подсказки.
struct SuflerView: View {
    @ObservedObject private var sufler = SuflerService.shared
    @ObservedObject private var processing = MeetingProcessingService.shared
    @ObservedObject private var tasksSvc = TasksService.shared
    @ObservedObject private var calendar = CalendarService.shared
    @ObservedObject private var navigation = WorkspaceNavigation.shared
    @AppStorage("charoite.calendarBriefs") private var calendarBriefs = false
    @State private var question = ""
    @State private var archiveAnswer = ""      // ответ по архиву, когда встреча не идёт
    @State private var lastArchiveQuestion = ""  // для «сохранить в граф»
    // прошлые ответы: раньше стирались новым вопросом, потом жили только
    // до перезапуска — теперь персист на диске (Application Support)
    @ObservedObject private var history = ArchiveHistoryStore.shared
    @State private var isSearchingArchive = false
    @State private var showFirstRun = false
    @AppStorage("charoit.firstRunSeen") private var firstRunSeen = false
    // чат встроен в суфлёр (панель справа); видимость запоминается
    @AppStorage("sufler.showChat") private var showChat = false
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            meetingCueBar
            // Чат — сосед сплита в HStack, НЕ третья колонка HSplitView (тот
            // не пересчитывает ширины при добавлении панели — чат открывался
            // за краем окна) и НЕ оверлей (перекрывал «Ответ по архиву» и
            // Claude — видно должно быть ВСЁ сразу, ничего не перекрыто).
            // Фиксированные 430 у чата + ужимающийся сплит; узкому окну
            // minWidth ниже даёт команду дорасти, чтобы место было всем.
            // (.inspector не вариант: на этой macOS валит приложение
            // constraints-циклом — NSGenericException, два краша.)
            HStack(spacing: 0) {
                HSplitView {
                    transcriptPane
                        .frame(minWidth: 320, idealWidth: 460)
                    rightPane
                        .frame(minWidth: 300, idealWidth: 400)
                }
                if showChat {
                    Divider()
                    LocalChatView()
                        .frame(width: 430)
                        .transition(.move(edge: .trailing))
                }
            }
            .animation(.spring(response: 0.3, dampingFraction: 0.85), value: showChat)
            // Вопрос здесь относится только к живому разговору. Между
            // встречами единый ввод находится в разделе «Память».
            if sufler.isRunning {
                Divider()
                askBar
            }
        }
        // 1080 при открытом чате: сплиту нужно ≥620 (минимумы панелей) плюс
        // 430 чата — узкое окно само дорастает, и никто никого не перекрывает
        .frame(minWidth: showChat ? 1080 : 700, minHeight: 460)
        // Окно закрыли — запись продолжается. Приложение живёт в меню-баре, и
        // ⌘W посреди совещания (убрать окно с экрана при демонстрации) не
        // должен стоить встречи. Выход из приложения — другое дело: там
        // applicationShouldTerminate спрашивает подтверждение.
        .onDisappear { }
        .sheet(isPresented: $showFirstRun) {
            FirstRunView { sufler.start() }
        }
        // Идёт запись — подсказка о начале встречи молчит; остановились —
        // снова имеет смысл (следующая встреча дня).
        .onChange(of: sufler.isRunning) { _, running in
            CalendarService.shared.recording(running)
        }
        .onAppear {
            // Первый запуск: объясняем, что это и зачем микрофон, ДО того как
            // человек нажмёт «Слушать встречу» и получит системный запрос.
            if !firstRunSeen { showFirstRun = true }
            TasksService.shared.rescan()   // бейдж «Задачи · N» актуален сразу
            ArchiveHistoryStore.shared.load()
            CalendarService.shared.recording(sufler.isRunning)
            // Dev-хуки скринов/смоков: на живой машине владельца клавиатурный
            // ввод в чужое окно проигрывает гонку за фокус — вопрос и окна
            // задаются окружением и выполняются сами.
            let env = ProcessInfo.processInfo.environment
            if let q = env["CHAROITE_ASK"], !q.isEmpty {
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { askArchive(q) }
            }
            if env["CHAROITE_OPEN_TASKS"] == "1" {
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { navigation.open(.tasks) }
            }
        }
    }

    // Вопрос работает ВСЕГДА, а не только во время встречи.
    //
    // Раньше кнопка блокировалась при остановленном демоне — и самый частый
    // вопрос («что обсуждали вчера?») было физически не задать: ради ответа про
    // вчерашнюю встречу приходилось запускать запись сегодняшней. Теперь идёт
    // живая встреча — спрашиваем демона (он видит стенограмму), не идёт —
    // ищем по архиву встреч и графу через Чароит.
    private var tasksOpen: Int { tasksSvc.openCount }

    /// «Встреча началась — начать запись?» Полоса вместо системного
    /// уведомления: разрешение на уведомления просить ради этого не хочется, а
    /// человек, у которого идёт встреча, смотрит в экран.
    ///
    /// Запись не включается сама ни при каком исходе: решение остаётся за
    /// человеком, и «Не сейчас» по этой встрече больше не спрашивают. Когда
    /// подсказки нет, полосы нет вовсе — пустого места она не занимает.
    @ViewBuilder
    private var meetingCueBar: some View {
        if !sufler.isRunning, let cue = calendar.cue {
            HStack(spacing: 10) {
                Image(systemName: "record.circle").foregroundStyle(Theme.accent)
                Text(cue.prompt).font(.callout)
                Spacer(minLength: 8)
                Button(L.t("Начать запись", "Start recording", "开始录制")) {
                    CalendarService.shared.dismissCue()
                    sufler.start()
                }
                .buttonStyle(.borderedProminent)
                .tint(Theme.accent)
                Button(L.t("Не сейчас", "Not now", "暂不")) {
                    CalendarService.shared.dismissCue()
                }
                .help(L.t("Про эту встречу больше не спросим",
                          "We will not ask about this meeting again",
                          "不会再就这场会议询问"))
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(Theme.accent.opacity(0.08))
            Divider()
        }
    }

    private var askBar: some View {
        HStack(spacing: 10) {
            Image(systemName: "questionmark.bubble")
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)   // декоративная, рядом есть поле с подписью
            TextField(sufler.isRunning
                      ? L.t("Спросить по этой встрече и графу…", "Ask about this meeting and the graph…", "就本次会议和图谱提问…")
                      : L.t("Что обсуждали на встрече вчера?  ·  спросить по архиву встреч", "What did we discuss yesterday?  ·  ask the meeting archive", "昨天的会议讨论了什么？ · 向会议档案提问"),
                      text: $question)
                .textFieldStyle(.plain)
                .onSubmit { submitQuestion() }
            DictationButton(text: $question)
            if isSearchingArchive {
                ProgressView().controlSize(.small)
            }
            // календарь: одна кнопка — бриф к ближайшему событию по архиву
            if !sufler.isRunning, calendarBriefs, let ev = calendar.nextEventTitle {
                Button {
                    askArchive(ev, brief: true)
                } label: {
                    Label(String(ev.prefix(28)), systemImage: "calendar")
                }
                .help(L.t("Бриф к ближайшей встрече: «\(ev)»", "Brief for the next event: “\(ev)”", "下一场会议的简报：「\(ev)」"))
            }
            // Подготовка ко встрече: та же архивная механика, но бриф-формат
            // (статус, решено, открыто, люди) вместо ответа на вопрос.
            if !sufler.isRunning {
                Button(L.t("К встрече", "Prep", "备会")) { submitBrief() }
                    .disabled(question.trimmingCharacters(in: .whitespaces).isEmpty || isSearchingArchive)
                    .help(L.t("Бриф для подготовки: статус темы, что решено, что открыто, кто вовлечён", "Prep brief: topic status, what's decided, what's open, who's involved", "备会简报：主题状态、已决定、待解决、相关人员"))
            }
            Button(L.t("Спросить", "Ask", "提问")) { submitQuestion() }
                .buttonStyle(.borderedProminent)
                .tint(Theme.accent)
                .disabled(question.trimmingCharacters(in: .whitespaces).isEmpty || isSearchingArchive)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }

    private func submitBrief() {
        let topic = question.trimmingCharacters(in: .whitespaces)
        guard !topic.isEmpty else { return }
        question = ""
        askArchive(topic, brief: true)
    }

    private func submitQuestion() {
        let q = question.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return }
        question = ""
        if sufler.isRunning {
            sufler.ask(q)
        } else {
            askArchive(q)
        }
    }

    /// Ответ по архиву встреч: ищем в графе и показываем в той же панели
    /// «Подсказка», чтобы результат появлялся там же, где во время встречи.
    ///
    /// Двухшаговый: vault_search достаёт сырьё (жирные сниппеты — ТОЛЬКО для
    /// модели), qwen пишет структурный ответ. Человеку сырые куски графа с
    /// YAML-frontmatter не показываются вовсе — по опыту это «каша»: финал =
    /// прямой ответ → факты по датам → нерешённое → список источников.
    ///
    /// brief: тот же контур, но формат «подготовка ко встрече» — статус темы,
    /// решено, открыто, люди. Один и тот же поиск, разные инструкции синтеза.
    private func askArchive(_ q: String, brief: Bool = false) {
        if !archiveAnswer.isEmpty, !lastArchiveQuestion.isEmpty,
           !archiveAnswer.hasPrefix(L.t("Нашёл источников", "Sources found", "已找到来源")) {
            history.append(q: lastArchiveQuestion, a: archiveAnswer)
        }
        isSearchingArchive = true
        archiveAnswer = ""
        lastArchiveQuestion = q
        Task {
            defer { Task { @MainActor in isSearchingArchive = false } }
            // 1200 знаков на файл: модель отвечает по содержимому, а не по
            // обрезкам (на коротких сниппетах честно пишет «информации нет»)
            var found = await ArchiveSearch.search(query: q, limit: 5, snippet: 1200,
                                                   budget: ArchiveSearch.defaultBudget)
            // маркер слабых совпадений: показываем честно и просим модель
            // не сочинять — «в архиве об этом нет» лучше выдуманного ответа
            let lowConfidence = found.hasPrefix(ArchiveSearch.lowConfidenceMarker)
            if lowConfidence { found.removeFirst() }
            guard !found.isEmpty else {
                await MainActor.run {
                    archiveAnswer = L.t("В графе ничего не нашлось по запросу. ", "Nothing matched in the graph. ", "图谱中没有匹配结果。")
                        + L.t("Проверь путь установки в Настройках (graph_dir в config.yaml).", "Check the install path in Settings (graph_dir in config.yaml).", "请在设置中检查安装路径（config.yaml 的 graph_dir）。")
                }
                return
            }
            // компактный список источников: строки выдачи «• путь/файл.md»
            let sources = found.split(separator: "\n")
                .filter { $0.hasPrefix("• ") }
                .map { String($0.dropFirst(2)).replacingOccurrences(of: ".md", with: "") }
            let sourceBlock = sources.isEmpty ? "" : L.t("\n\nИсточники:\n", "\n\nSources:\n", "\n\n来源：\n")
                + sources.map { "· \($0)" }.joined(separator: "\n")
            // прогресс: человек видит, ЧТО нашлось, но не сырые куски
            let confNote = lowConfidence
                ? L.t("⚠ Совпадения слабые — возможно, в архиве этого нет.\n",
                  "⚠ Weak matches — this may not be in the archive.\n",
                  "⚠ 匹配较弱——档案中可能没有这条。\n") : ""
            await MainActor.run {
                archiveAnswer = confNote
                    + L.t("Нашёл источников: \(sources.count) — формулирую ответ…", "Sources found: \(sources.count) — composing the answer…", "已找到来源：\(sources.count) — 正在组织回答…") + sourceBlock
            }
            let instruction = brief
                ? L.t("Готовлюсь к встрече по теме: \(q)\n\nФрагменты из архива встреч:\n\(found)\n\n", "Preparing for a meeting on: \(q)\n\nFragments from the meeting archive:\n\(found)\n\n", "正在为会议做准备，主题：\(q)\n\n会议档案片段：\n\(found)\n\n")
                    + L.t("Собери бриф для подготовки, по-русски, телеграфно, строго блоками. ", "Build a prep brief, in English, telegraphic, strictly in blocks. ", "编写备会简报，用中文，电报式，严格分块。")
                    + L.t("«Статус:» — 1-2 строки, где тема сейчас. ", "\"Status:\" — 1-2 lines, where the topic stands. ", "「状态：」——1-2 行，主题现状。")
                    + L.t("«Решено:» — пункты «— дата: что» от старого к новому. ", "\"Decided:\" — items \"— date: what\", oldest to newest. ", "「已决定：」——条目「— 日期：内容」，从旧到新。")
                    + L.t("«Открыто:» — нерешённые вопросы и риски, пункты. ", "\"Open:\" — unresolved questions and risks, items. ", "「待解决：」——未决问题与风险，条目。")
                    + L.t("«Люди:» — кто вовлечён и чем занят, из фрагментов, одной строкой на человека; никого нет — блок не пиши. Только факты из фрагментов, ничего не выдумывай, без вступлений и воды. Если фрагменты не про эту тему — одна строка: по теме в архиве пусто.", "\"People:\" — who is involved and doing what, from the fragments, one line per person; nobody — skip the block. Only facts from the fragments, invent nothing, no intros or filler. If the fragments are off-topic — one line: nothing on this topic in the archive.", "「相关人员：」——谁在参与、在做什么，出自片段，每人一行；没有人就不写该块。只用片段中的事实，不得编造，不要开场白和废话。若片段与主题无关——用一行说明：档案中没有该主题。")
                : L.t("Вопрос: \(q)\n\nФрагменты из архива встреч:\n\(found)\n\n", "Question: \(q)\n\nFragments from the meeting archive:\n\(found)\n\n", "问题：\(q)\n\n会议档案片段：\n\(found)\n\n")
                    + L.t("Составь ответ строго такой структуры, по-русски, телеграфно. ", "Compose the answer in exactly this structure, in English, telegraphic. ", "严格按此结构作答，用中文，电报式。")
                    + L.t("Первая строка — прямой ответ на вопрос, 1-2 предложения, без ", "First line — a direct answer, 1-2 sentences, without ", "第一行——直接回答，1-2 句，不要")
                    + L.t("нумерации и префиксов. Затем блок «Факты:» — пункты вида «— дата: что решили/что случилось (кто)», хронологически от старого к новому; даты бери из фрагментов. Если по фрагментам что-то осталось нерешённым — блок «Открыто:» с пунктами, иначе ", "numbering or prefixes. Then a \"Facts:\" block — items like \"— date: what was decided/what happened (who)\", oldest to newest; take dates from the fragments. If something remains unresolved — an \"Open:\" block with items, otherwise ", "编号和前缀。然后是「事实：」块——条目形如「— 日期：决定了什么/发生了什么（谁）」，从旧到新；日期取自片段。若仍有未决事项——写「待解决：」块，否则")
                    + L.t("его не пиши. Только факты из фрагментов, ничего не выдумывай. ", "don't write it. Only facts from the fragments, invent nothing. ", "就不要写。只用片段中的事实，不得编造。")

                    + L.t("Без вступлений, без воды, без markdown-заголовков. Если ответа ", "No intros, no filler, no markdown headings. If the answer is ", "不要开场白、废话和 markdown 标题。如果答案")
                    + L.t("во фрагментах нет — одна строка: чего именно не хватает.", "not in the fragments — one line: what exactly is missing.", "片段中没有——用一行说明缺什么。")
            // стрим: токены сразу в панель (троттлинг кадров — в StreamThrottler-стиле
            // не нужен: панель обновляется снапшотом полного текста, ~разы в сек)
            var lastPaint = Date.distantPast
            let answer = await ArchiveSearch.ask(
                question: instruction,
                system: L.t("Ты — ассистент по архиву рабочих встреч. ", "You are an assistant over a work-meeting archive. Answer in English. ", "你是工作会议档案助手。用中文回答。")
                    + L.t("Отвечаешь только по приведённым фрагментам, без домыслов, телеграфно.", "Answer only from the given fragments, no speculation, telegraphic style.", "只依据给出的片段回答，不臆测，电报式简洁。"),
                model: LocalChatService.shared.model,
                ollama: AppSettings.ollamaURL) { partial in
                let now = Date()
                guard now.timeIntervalSince(lastPaint) > 0.12 else { return }
                lastPaint = now
                Task { @MainActor in
                    archiveAnswer = confNote + partial + "▌"
                }
            }
            let trimmed = answer.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else {
                // синтез не удался — хотя бы сырьё, лучше каша, чем пустота
                await MainActor.run { archiveAnswer = confNote + found }
                return
            }
            // плашка неуверенности живёт и в ФИНАЛЬНОМ ответе: раньше она
            // показывалась только в прогрессе и исчезала после синтеза
            await MainActor.run { archiveAnswer = confNote + trimmed + sourceBlock }
        }
    }

    /// Ответ по архиву → заметка в графе: Заметки/YYYY-MM-DD_HHMM_Вопрос.md.
    /// Обратные [[ссылки]]源 из текста сохраняются — узлы графа свяжутся сами.
    private func saveAnswerToGraph() {
        guard let graph = AppSettings.graphDir else { return }
        let dir = graph.appendingPathComponent(L.t("Заметки", "Notes", "笔记"))
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd_HHmm"
        let stamp = fmt.string(from: Date())
        let safeQ = lastArchiveQuestion
            .replacingOccurrences(of: "[/\\:*?\"<>|]", with: "-", options: .regularExpression)
            .prefix(60)
        let name = safeQ.isEmpty ? L.t("Ответ по архиву", "Archive answer", "档案回答") : String(safeQ)
        let url = dir.appendingPathComponent("\(stamp)_\(name).md")
        let body = "# \(lastArchiveQuestion.isEmpty ? L.t("Ответ по архиву", "Archive answer", "档案回答") : lastArchiveQuestion)\n\n"
            + L.t("*Сохранено из Charoite \(stamp.replacingOccurrences(of: "_", with: " "))*\n\n",
                    "*Saved from Charoite \(stamp.replacingOccurrences(of: "_", with: " "))*\n\n",
                    "*由 Charoite 保存 \(stamp.replacingOccurrences(of: "_", with: " "))*\n\n")
            + archiveAnswer + "\n"
        try? body.write(to: url, atomically: true, encoding: .utf8)
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    /// Открыть чат и ВЫНЕСТИ ЕГО ВПЕРЁД.
    ///
    /// `openWindow` только создаёт окно; если оно уже открыто и лежит за окном
    /// суфлёра, кнопка внешне «не работает» — окно есть, но остаётся сзади.
    /// Поэтому дополнительно активируем приложение и поднимаем само окно.
    private func openChatWindow() {
        openWindow(id: "localchat")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) {
            NSApp.activate(ignoringOtherApps: true)
            let chat = NSApp.windows.first { w in
                w.identifier?.rawValue.contains("localchat") == true || w.title == L.t("Локальный чат", "Local chat", "本地聊天")
            }
            chat?.makeKeyAndOrderFront(nil)
        }
    }

    /// Открыть папку со встречами.
    ///
    /// Адрес архива в iCloud раньше был вписан в код одной строкой, без всякой
    /// проверки: если графа с таким именем нет (а он есть только у автора),
    /// кнопка просто ничего не делала — ни папки, ни объяснения. Теперь
    /// собираем кандидатов по всем vault'ам и открываем тот архив, где жизнь
    /// ПОСЛЕДНЯЯ по времени: графов может быть несколько (личный, рабочий), и
    /// «первый существующий» вёл в личный с одной встречей, пока вся работа
    /// шла в соседнем. Стенограммы суфлёра — последний рубеж.
    private func openMeetingsFolder() {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let icloud = home.appendingPathComponent(
            "Library/Mobile Documents/iCloud~md~obsidian/Documents")
        var candidates: [URL] = []
        // граф встреч в Obsidian: любая папка «Встречи-архив»/«Встречи» в vault
        if let vaults = try? FileManager.default.contentsOfDirectory(
            at: icloud, includingPropertiesForKeys: nil) {
            for v in vaults {
                candidates.append(v.appendingPathComponent(L.t("Встречи-архив", "Meeting archive", "会议档案")))
                candidates.append(v.appendingPathComponent(L.t("Встречи", "Meetings", "会议")))
            }
        }
        candidates.append(AppSettings.charoiteRoot.appendingPathComponent("transcripts"))

        let fm = FileManager.default
        // самая свежая правка ВНУТРИ папки — надёжнее даты самой папки: iCloud
        // трогает каталоги при синке, а нам важно, где недавно писали встречу
        func lastActivity(_ dir: URL) -> Date {
            let items = (try? fm.contentsOfDirectory(
                at: dir, includingPropertiesForKeys: [.contentModificationDateKey])) ?? []
            return items
                .compactMap { try? $0.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate }
                .max() ?? .distantPast
        }
        let existing = candidates.filter { fm.fileExists(atPath: $0.path) }
        if let best = existing.max(by: { lastActivity($0) < lastActivity($1) }) {
            NSWorkspace.shared.open(best)
            return
        }
        if fm.fileExists(atPath: icloud.path) {
            NSWorkspace.shared.open(icloud)
            return
        }
        // совсем ничего нет — честно говорим, а не молчим
        let alert = NSAlert()
        alert.messageText = L.t("Папка со встречами пока не создана", "The meetings folder does not exist yet", "会议文件夹尚未创建")
        alert.informativeText = L.t("Она появится после первой записанной встречи — ", "It appears after the first recorded meeting — ", "首次录制会议后即会出现——")
            + L.t("нажмите «Слушать встречу».", "press \"Listen to meeting\".", "请点击「旁听会议」。")
        alert.addButton(withTitle: L.t("Понятно", "OK", "知道了"))
        alert.runModal()
    }

    /// Статус про сбой, а не про обычный ход дела.
    ///
    /// Признак приходит из сервиса. Поиск подстрок в локализованном тексте,
    /// стоявший здесь раньше, работал только по-русски: английское
    /// «Recording dropped — recovering» не содержало ни «прервалась», ни
    /// «Failed», и сообщение об оборванной записи показывалось мелким серым
    /// текстом в одну строку — ровно то, чего этот код должен избегать.
    private var displayedStatus: String {
        if !sufler.isRunning, let processingStatus = processing.statusText {
            return processingStatus
        }
        return sufler.status
    }

    private var statusIsProblem: Bool {
        if !sufler.isRunning, processing.statusText != nil {
            return processing.isError
        }
        return sufler.statusIsError
    }

    /// Что показывать в панели: во время встречи — подсказку демона, вне
    /// встречи — ответ по архиву. Пусто — приглашение спросить.
    private var paneText: AttributedString {
        if sufler.isRunning {
            // Нить — единственное, что здесь показывается: ответы и тезисы
            // демон вплетает в неё сам. Ручная подсказка (⌘⏎) остаётся
            // исключением: её просят, когда ждут ответ прямо сейчас, и она
            // гаснет со следующим обновлением нити.
            if !sufler.hint.isEmpty { return withBoldQuestions(sufler.hint) }
            if !sufler.thread.isEmpty { return withThreadMarks(sufler.thread) }
            return AttributedString(L.t("Нить встречи появится через минуту разговора · ⌘⏎ — подсказка сейчас",
                                        "The meeting thread appears after a minute of talk · ⌘⏎ — hint now",
                                        "会议脉络将在交谈一分钟后出现 · ⌘⏎ — 立即提示"))
        }
        if archiveAnswer.isEmpty {
            // при открытом чате нижнего поля нет — не отправляем в никуда
            return AttributedString(showChat
                ? L.t("Ответы по архиву появятся здесь. Спросить — в чате справа или закрой Чат для поля внизу", "Archive answers appear here. Ask in the chat on the right, or close Chat for the field below", "档案回答会显示在这里。在右侧聊天提问，或关闭聊天使用下方输入框")
                : L.t("Спроси про прошлые встречи в поле внизу — найду по архиву и графу", "Ask about past meetings in the field below — I'll search the archive and graph", "在下方输入框询问过往会议——我会检索档案与图谱"))
        }
        return withBoldQuestions(archiveAnswer)
    }

    private var paneIsPlaceholder: Bool {
        sufler.isRunning ? (sufler.hint.isEmpty && sufler.thread.isEmpty)
                         : archiveAnswer.isEmpty
    }

    /// Нить: знаки строк ведут глаз, служебное приглушено.
    ///
    /// Полотно читают боковым зрением, поэтому вес несут не абзацы, а знаки:
    /// «●» тема, «⚑» решение, «⚡» ответ, «?» вопрос, «⏮» хвост из архива.
    /// Реплики разговора («-») остаются обычными — их много, и жирный шрифт
    /// на них превратил бы полотно в кашу.
    private func withThreadMarks(_ raw: String) -> AttributedString {
        var out = AttributedString()
        for (i, line) in raw.components(separatedBy: "\n").enumerated() {
            if i > 0 { out.append(AttributedString("\n")) }
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            var piece = AttributedString(line)
            if trimmed.hasPrefix("●") {
                piece.font = .callout.bold()
            } else if trimmed.hasPrefix("⚑") || trimmed.hasPrefix("📌") {
                piece.font = .callout.weight(.medium)
                piece.foregroundColor = .orange
            } else if trimmed.hasPrefix("⚡") {
                piece.font = .callout.weight(.medium)
            } else if trimmed.hasPrefix("?") {
                piece.foregroundColor = .secondary
            } else if trimmed.hasPrefix("⏮") || trimmed.hasPrefix("💭") {
                piece.foregroundColor = .secondary
            }
            out.append(piece)
        }
        return out
    }

    /// Вопрос в панели — жирным, ответ обычным.
    ///
    /// Демон помечает вопросы значком «❓» (и в подсказке, и в облаке). Без
    /// выделения вопрос сливается с ответом, и в потоке текста непонятно, где
    /// заканчивается одно и начинается другое.
    private func withBoldQuestions(_ raw: String) -> AttributedString {
        var out = AttributedString()
        for (i, line) in raw.components(separatedBy: "\n").enumerated() {
            if i > 0 { out.append(AttributedString("\n")) }
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            var piece: AttributedString
            if trimmed.hasPrefix("❓") {
                piece = AttributedString(line)
                piece.font = .callout.bold()
            } else if trimmed.hasPrefix("· "), let url = obsidianURL(String(trimmed.dropFirst(2))) {
                // источник ответа — ссылка: клик открывает заметку в Obsidian
                piece = AttributedString(line)
                piece.link = url
                piece.foregroundColor = .accentColor
                piece.underlineStyle = .single
            } else {
                // модель пишет **жирное»/`код` — рендерим, а не показываем звёздочки
                piece = MarkdownLine.render(String(line))
            }
            out.append(piece)
        }
        return out
    }

    /// obsidian://open на заметку графа. Имя вольта — родитель папки графа
    /// (стандартная раскладка: vault/граф/заметка.md).
    private func obsidianURL(_ rel: String) -> URL? {
        guard let graph = AppSettings.graphDir else { return nil }
        let vault = graph.deletingLastPathComponent().lastPathComponent
        let file = graph.lastPathComponent + "/" + rel
        var comps = URLComponents()
        comps.scheme = "obsidian"
        comps.host = "open"
        comps.queryItems = [URLQueryItem(name: "vault", value: vault),
                            URLQueryItem(name: "file", value: file)]
        return comps.url
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 12) {
            Button {
                sufler.isRunning ? sufler.stop() : sufler.start()
            } label: {
                Label {
                    Text(sufler.isRunning ? L.t("Стоп", "Stop", "停止") : L.t("Слушать встречу", "Listen to meeting", "聆听会议"))
                } icon: {
                    Image(systemName: sufler.isRunning ? "stop.circle.fill" : "waveform.circle.fill")
                        // живая волна на записи — видно СРАЗУ, что слушаем,
                        // без мигающих лампочек
                        .symbolEffect(.variableColor.iterative, options: .repeating,
                                      isActive: sufler.isRunning)
                }
                .font(.headline)
                // не переносить: в тесном тулбаре (4 тумблера) главная кнопка
                // ломалась в «Слу-шать встр ечу» на три строки
                .fixedSize()
                .foregroundStyle(.white)
                .padding(.horizontal, 14)
                .padding(.vertical, 7)
                .background(
                    Capsule().fill(sufler.isRunning
                                   ? AnyShapeStyle(Color.red)
                                   : AnyShapeStyle(Theme.brand))
                        .shadow(color: Theme.accent.opacity(sufler.isRunning ? 0 : 0.35),
                                radius: 6, y: 2)
                )
            }
            .buttonStyle(.plain)
            .keyboardShortcut(.space, modifiers: [.command, .shift])

            // Часы записи. Пульсирующая волна говорит «работает», но не
            // говорит «сколько уже» — а человек, вернувшийся к ноутбуку,
            // спрашивает именно это. Моноширинные цифры, чтобы строка не
            // дёргалась каждую секунду.
            if sufler.isRunning {
                Text(SuflerService.clockText(sufler.recordingElapsed))
                    .font(.headline.monospacedDigit())
                    .foregroundStyle(.red)
                    .accessibilityLabel(L.t("Идёт запись", "Recording", "录音中"))
                    .accessibilityValue(SuflerService.clockText(sufler.recordingElapsed))
            }

            // Сбой записи нельзя показывать так же, как «Готов к запуску»:
            // мелкий серый текст в одну строку человек на встрече не заметит, а
            // сообщение «нажмите ещё раз» вдобавок обрезалось на полуслове.
            Text(displayedStatus)
                .font(statusIsProblem ? .caption.weight(.medium) : .caption)
                .foregroundStyle(statusIsProblem ? Color.red : Color.secondary)
                .lineLimit(statusIsProblem ? 2 : 1)
                .fixedSize(horizontal: false, vertical: statusIsProblem)
                .textSelection(.enabled)

            if !sufler.isRunning, processing.isProcessing {
                ProgressView().controlSize(.small)
            }
            if !sufler.isRunning, let actionTitle = processing.actionTitle {
                Button(actionTitle) { processing.openResult() }
                    .controlSize(.small)
            }
            // Ошибка — не тупик: стенограмма цела, конвейер перезапускаем
            // отсюда же, где показана сама ошибка. До этой кнопки повтор
            // существовал только как имя скрипта в терминале.
            if !sufler.isRunning, processing.canRetry || processing.retryInFlight {
                Button(L.t("Повторить обработку", "Retry processing", "重新处理")) {
                    processing.retry()
                }
                .controlSize(.small)
                // пока прошлый повтор жив — кнопка гаснет, но остаётся на
                // месте: исчезающая кнопка под курсором читается как сбой,
                // а два конвейера на одну встречу пишут один статус и лог
                .disabled(processing.retryInFlight)
            }

            Spacer()

            // Живые тумблеры: выключил на этой встрече — станет дефолтом следующих.
            // Подписи текстом, а не эмодзи: «⚡» и «☁️» в Toggle(.switch) на маке
            // вообще не рисуются — на экране оставались три одинаковых
            // переключателя, и угадать, который за что, было невозможно.
            // accessibilityLabel обязателен отдельно от видимой подписи:
            // VoiceOver читает Toggle как безымянный «флажок», текст рядом с ним
            // в озвучку не попадает (проверено обходом AX-дерева).
            // fixedSize обязателен: в узкой панели SwiftUI ломал подписи по
            // буквам — на экране стояло «По-дс-ка-зки» в четыре строки.
            // Пусть лучше панель прокрутится, чем слово рассыплется.
            // Чипы слоёв вместо системных свитчей (UI_REVISION_2026-08,
            // правило 1): три Toggle(.switch) в одну строку красили полосу
            // в системный синий и спорили с фирменной кнопкой записи. Чип
            // несёт цвет слоя: индиго — локальное, sky — единственный слой,
            // который уходит с машины.
            LayerBar {
                LayerChip(title: L.t("Подсказки", "Hints", "提示"), isOn: $sufler.hintsOn)
                    .help(L.t("Подсказки и мгновенные ответы на вопросы собеседника", "Hints and instant answers to the other side's questions", "提示与对方提问的即时回答"))
                    .accessibilityLabel(L.t("Подсказки во время встречи", "Hints during the meeting", "会议期间的提示"))
                    .accessibilityHint(L.t("Мгновенные ответы на вопросы собеседника", "Instant answers to the other side's questions", "对方提问的即时回答"))
                LayerChip(title: L.t("Тезисы", "Theses", "要点"), isOn: $sufler.thesesOn)
                    .help(L.t("Автотезисы 📌💭 и дежавю ⏮ по ходу встречи", "Auto-theses 📌💭 and déjà vu ⏮ during the meeting", "会议中的自动要点 📌💭 与似曾相识 ⏮"))
                    .accessibilityLabel(L.t("Автотезисы", "Auto-theses", "自动要点"))
                    .accessibilityHint(L.t("Ключевые мысли и повторы по ходу встречи", "Key thoughts and repetitions during the meeting", "会议中的关键想法与重复内容"))
                LayerChip(title: "Claude", isOn: $sufler.cloudOn, tint: Theme.sky)
                    .help(L.t("Параллельные ответы Claude на вопросы собеседника", "Parallel Claude answers to the other side's questions", "Claude 并行回答对方的提问"))
                    .accessibilityLabel(L.t("Ответы Claude", "Claude answers", "Claude 回答"))
                    .accessibilityHint(L.t("Параллельные ответы облачной модели", "Parallel answers from the cloud model", "云端模型的并行回答"))
            }
            .fixedSize(horizontal: true, vertical: false)

            Button {
                showChat.toggle()
            } label: {
                Label(L.t("Чат", "Chat", "聊天"), systemImage: showChat ? "message.fill" : "message")
            }
            .help(L.t("Локальный чат с памятью — панель прямо в окне суфлёра", "Local chat with memory — a pane right in the copilot window", "带记忆的本地聊天——直接嵌在提词窗口"))

            Button {
                openChatWindow()  // не просто открыть, а вынести вперёд
            } label: {
                Image(systemName: "arrow.up.forward.square")
            }
            .help(L.t("Чат отдельным окном (история общая с панелью)", "Chat in its own window (history shared with the pane)", "聊天独立窗口(与面板共用历史)"))

            // Список встреч: результат вчерашней записи не должен исчезать с
            // экрана в ту секунду, когда начата новая.
            if !processing.history.isEmpty {
                Button {
                    navigation.open(.meetings)
                } label: {
                    Label(L.t("Встречи", "Meetings", "会议"),
                          systemImage: "clock.arrow.circlepath")
                }
                .help(L.t("Последние записи: состояние, результат, повтор обработки",
                          "Recent recordings: state, result, retry processing",
                          "最近的录音：状态、结果、重新处理"))
            }

            Button {
                TasksService.shared.rescan()
                navigation.open(.tasks)
            } label: {
                // бейдж открытых поручений: видно, что по встречам есть хвосты
                if tasksOpen > 0 {
                    Label(L.t("Задачи · \(tasksOpen)", "Tasks · \(tasksOpen)", "任务 · \(tasksOpen)"), systemImage: "checklist")
                } else {
                    Label(L.t("Задачи", "Tasks", "任务"), systemImage: "checklist")
                }
            }
            .help(L.t("Поручения со встреч (чекбоксы из минуток и заметок графа)", "Meeting action items (checkboxes from minutes and graph notes)", "会议行动项（来自纪要和图谱笔记的复选框）"))

            Button {
                openMeetingsFolder()
            } label: {
                Label(L.t("Встречи", "Meetings", "会议"), systemImage: "folder")
            }
            .help(L.t("Открыть встречи в Finder (стенограммы, тезисы, минутки, разборы)", "Open meetings in Finder (transcripts, theses, minutes, debriefs)", "在 Finder 打开会议（逐字稿、要点、纪要、复盘）"))

            // Действия записи живут ТОЛЬКО во время встречи: вне её это были
            // три вечно серые кнопки, занимавшие треть тулбара. Появляются
            // мягко вместе со стартом записи.
            if sufler.isRunning {
                Button(L.t("Подсказка", "Hint", "提示")) { sufler.requestHint() }
                    .keyboardShortcut(.return, modifiers: .command)
                    .disabled(sufler.isHinting)
                    .help(L.t("Подсказка по последним минутам (⌘⏎)", "Hint on the last minutes (⌘⏎)", "按最近几分钟提示（⌘⏎）"))

                Button("Claude") { sufler.requestCloud() }
                    .keyboardShortcut(.return, modifiers: [.command, .shift])
                    .disabled(sufler.isClouding || !sufler.cloudOn)
                    // адрес в подсказке — тот же тулбар, а не Настройки: облака
                    // там нет, а тумблер стоит левее этой же кнопки
                    .help(sufler.cloudOn
                          ? L.t("Спросить Claude по ходу встречи — кусок стенограммы уйдёт в облако (⌘⇧⏎)", "Ask Claude mid-meeting — a transcript slice goes to the cloud (⌘⇧⏎)", "会议中问 Claude — 一段逐字稿将发送至云端（⌘⇧⏎）")
                          : L.t("Облако выключено: включите «Claude» в тулбаре. Стенограмма не покидает машину", "Cloud is off: enable “Claude” in the toolbar. The transcript never leaves this machine", "云端已关闭：在工具栏开启「Claude」。逐字稿不会离开本机"))

                Button { sufler.requestExpand() } label: {
                    HStack(spacing: 4) {
                        if sufler.isExpanding { ProgressView().controlSize(.small) }
                        Text("⏮")
                    }
                }
                    .keyboardShortcut("e", modifiers: [.command, .shift])
                    .disabled(sufler.isExpanding)
                    .help(L.t("Что было по текущей теме на прошлых встречах — из архива в нить (⌘⇧E)",
                              "What past meetings said on the current topic — from the archive into the thread (⌘⇧E)",
                              "过往会议对当前话题的讨论——从档案写入脉络（⌘⇧E）"))

                Button(L.t("Протокол", "Minutes", "纪要")) { sufler.requestSummary() }
                    .disabled(sufler.isHinting)
                    .help(L.t("Собрать протокол встречи прямо сейчас", "Build the meeting minutes right now", "立即生成会议纪要"))
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .animation(.easeInOut(duration: 0.2), value: sufler.isRunning)
    }

    // MARK: - Левая панель: стенограмма

    private var transcriptPane: some View {
        VStack(alignment: .leading, spacing: 0) {
            paneTitle(L.t("Стенограмма", "Transcript", "逐字稿"), systemImage: "text.quote")
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        if sufler.lines.isEmpty {
                            SuflerEmptyState(symbol: sufler.isRunning ? "waveform" : "waveform.circle",
                                             text: sufler.isRunning ? L.t("Слушаю…", "Listening…", "聆听中…") : L.t("Нажми «Слушать встречу»", "Press “Listen to meeting”", "点按「聆听会议」"))
                        }
                        // Ритм чтения: реплики одного спикера идут плотно, смена
                        // спикера даёт воздух — глаз находит границы разговора
                        // без вчитывания. Таймкоды тише текста: они справка, не
                        // содержание. lineSpacing 2 — длинные абзацы STT дышат.
                        ForEach(Array(sufler.lines.enumerated()), id: \.element.id) { i, line in
                            let newSpeaker = i == 0
                                || (!line.speaker.isEmpty
                                    && sufler.lines[i - 1].speaker != line.speaker)
                            transcriptRow(line, topPad: newSpeaker && i > 0 ? 12 : 4)
                                .id(line.id)
                        }
                    }
                    .padding(12)
                }
                .onChange(of: sufler.lines.count) { _, _ in
                    if let lastId = sufler.lines.last?.id {
                        withAnimation(.easeOut(duration: 0.15)) {
                            proxy.scrollTo(lastId, anchor: .bottom)
                        }
                    }
                }
            }
        }
        .background(Color(nsColor: .textBackgroundColor))
    }

    // MARK: - Правая панель: тезисы + подсказка

    // Выключенная плашка убирает своё: ⚡ — карточку подсказки, Тезисы —
    // панель тезисов, ☁️ — облачную ленту. Подсказка и Claude живут в ОДНОЙ
    // панели (решение 28.07): локальная карточка сверху, облачные ответы
    // ниже sky-карточкой — граница «что ушло с машины» видна цветом,
    // а не отдельным окном. Пустых мёртвых панелей на экране нет.
    private var rightPane: some View {
        VSplitView {
            // Одно полотно вместо трёх панелей: тезисы, ответы ⚡/☁ и хвосты
            // архива вплетает в нить сам демон (src/meeting_thread.py). Пока
            // они жили порознь, автоответ раз в полминуты держал панель
            // подсказки непустой — и нить, главное на встрече, не показывалась
            // вовсе: 04.08 на экране была лента «вопрос — вопрос — уточните
            // вопрос», а разговора не было видно.

            // Архивные вопросы переехали в единый раздел «Память»; эта панель
            // теперь принадлежит только живому разговору. Нить видна всю
            // встречу независимо от тумблеров ⚡/☁ — демон вплетает в неё
            // и тезисы, и хвосты архива (решение 04.08, PR #232).
            if sufler.isRunning {
            VStack(alignment: .leading, spacing: 0) {
                HStack {
                    // подсказки выключены, облако включено — панель честно
                    // называется по единственному жильцу
                    paneTitle(sufler.isRunning
                                  ? L.t("Нить встречи", "Meeting thread", "会议脉络")
                                  : L.t("Ответ по архиву", "Archive answer", "档案回答"),
                              systemImage: sufler.isRunning
                                  ? "text.line.first.and.arrowtriangle.forward"
                                  : "clock.arrow.circlepath",
                              copy: { sufler.isRunning ? sufler.thread : archiveAnswer })
                    if sufler.isHinting || isSearchingArchive
                        || (sufler.isRunning && sufler.isClouding) {
                        ProgressView().controlSize(.small).padding(.trailing, 10)
                    }
                    // хороший ответ жалко терять: одной кнопкой — заметкой в граф
                    if !sufler.isRunning && !archiveAnswer.isEmpty && !isSearchingArchive {
                        Button {
                            saveAnswerToGraph()
                        } label: {
                            Image(systemName: "square.and.arrow.down.on.square")
                        }
                        .buttonStyle(.plain)
                        .foregroundStyle(.tertiary)
                        .help(L.t("Сохранить ответ заметкой в граф (Заметки/)", "Save the answer as a graph note (Notes/)", "将回答保存为图谱笔记（Заметки/）"))
                        .padding(.trailing, 10)
                    }
                }
                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(alignment: .leading, spacing: 0) {
                            Color.clear.frame(height: 1).id("hintTop")
                            if !sufler.isRunning || sufler.hintsOn {
                                Text(paneText)
                                    .font(.callout)
                                    .foregroundStyle(paneIsPlaceholder ? .tertiary : .primary)
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            // облачная лента — в той же панели, sky-карточкой:
                            // видно, ЧТО ушло с машины, без отдельного окна
                            if sufler.isRunning && sufler.cloudOn {
                                cloudCard
                                    .padding(.top, sufler.hintsOn ? 10 : 0)
                            }
                            // прошлые вопросы — свёрнуты, свежие сверху (персист)
                            if !sufler.isRunning && !history.entries.isEmpty {
                                Divider().padding(.vertical, 8)
                                ForEach(Array(history.entries.enumerated().reversed()), id: \.offset) { _, qa in
                                    DisclosureGroup {
                                        Text(withBoldQuestions(qa.a))
                                            .font(.callout)
                                            .textSelection(.enabled)
                                            .frame(maxWidth: .infinity, alignment: .leading)
                                            .padding(.top, 4)
                                    } label: {
                                        Text(qa.q)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(1)
                                    }
                                }
                            }
                            Color.clear.frame(height: 1).id("hintBottom")
                        }
                        .padding(12)
                    }
                    // стрим токенов: держимся за низ, без анимации — иначе дёргается
                    .onChange(of: sufler.hint) { _, _ in
                        DispatchQueue.main.async {
                            proxy.scrollTo("hintBottom", anchor: .bottom)
                        }
                    }
                    // лента Claude растёт вниз — держимся за низ и для неё
                    .onChange(of: sufler.cloud) { _, _ in
                        DispatchQueue.main.async {
                            proxy.scrollTo("hintBottom", anchor: .bottom)
                        }
                    }
                    // ответ по архиву — НЕ стрим: приходит целиком, ответ сверху,
                    // источники под ним. Скролл вниз (как у подсказок) оставлял
                    // на экране хвост источников, а сам ответ приходилось мотать.
                    .onChange(of: archiveAnswer) { _, _ in
                        DispatchQueue.main.async {
                            proxy.scrollTo("hintTop", anchor: .top)
                        }
                    }
                }
            }
            .frame(minHeight: 140)
            .background(Theme.accent.opacity(0.05))
            }

            if !sufler.thesesOn && !sufler.hintsOn && !sufler.cloudOn {
                Text(L.t("Все панели выключены — включи плашки сверху", "All panes are off — enable the chips above", "所有面板均已关闭——请启用上方开关"))
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }

    /// Строка стенограммы — отдельной функцией: конкатенация Text со стилями
    /// внутри ForEach валила type-checker SPM («unable to type-check in
    /// reasonable time»).
    private func transcriptRow(_ line: SuflerService.TranscriptLine, topPad: CGFloat) -> some View {
        let body: Text = line.speaker.isEmpty
            ? Text(line.text)
            : Text(line.speaker + "  ")
                .font(.callout.weight(.semibold))
                .foregroundStyle(Theme.accent)
              + Text(line.text)
        return HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(line.ts)
                .font(.caption2.monospaced())
                .foregroundStyle(.quaternary)
            body
                .font(.callout)
                .lineSpacing(2)
                .textSelection(.enabled)
        }
        .padding(.top, topPad)
    }

    /// Облачная лента внутри панели подсказки: sky-карточка — облачное
    /// заметно цветом (DESIGN.md), тумблер ☁️ остаётся единственным
    /// выключателем отправки стенограммы с машины.
    private var cloudCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: "cloud.fill")
                    .font(.caption2)
                Text("Claude")
                    .font(.caption2.weight(.semibold))
                Spacer()
                if !sufler.cloud.isEmpty {
                    SuflerCopyButton(text: { sufler.cloud })
                }
            }
            .foregroundStyle(Theme.sky)
            Text(sufler.cloud.isEmpty
                 ? AttributedString(L.t("Вопрос собеседника уйдёт Claude автоматически · ⌘⇧⏎ — вручную", "The other side's question goes to Claude automatically · ⌘⇧⏎ — manually", "对方的问题会自动发给 Claude · ⌘⇧⏎ — 手动发送"))
                 : withBoldQuestions(sufler.cloud))
                .font(.callout)
                .foregroundStyle(sufler.cloud.isEmpty ? .tertiary : .primary)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(10)
        .background(Theme.sky.opacity(0.07),
                    in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                .strokeBorder(Theme.sky.opacity(0.25), lineWidth: 1)
        }
    }

    // PaneHeader из DesignKit: капсовое имя с кернингом и нижним
    // разделителем — заголовок панели перестаёт путаться с подписью
    // внутри неё. Сигнатура прежняя, оба вызова не тронуты.
    private func paneTitle(_ title: String, systemImage: String,
                           copy: (() -> String)? = nil) -> some View {
        PaneHeader(title: title, systemImage: systemImage, count: nil) {
            if let copy {
                SuflerCopyButton(text: copy)
            }
        }
    }

}

#endif
