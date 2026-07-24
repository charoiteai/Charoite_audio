import AppKit
import SwiftUI

#if os(macOS)

/// Суфлёр: левая панель — стенограмма в реальном времени, правая — тезисы и подсказки.
struct SuflerView: View {
    @ObservedObject private var sufler = SuflerService.shared
    @State private var question = ""
    @State private var archiveAnswer = ""      // ответ по архиву, когда встреча не идёт
    @State private var isSearchingArchive = false
    @State private var showFirstRun = false
    @AppStorage("charoit.firstRunSeen") private var firstRunSeen = false
    // Архивный поиск (vault + граф) — выключаемый: не всем нужно, чтобы
    // приложение вне встреч ходило по личному vault. Чисто клиентский
    // тумблер, демону о нём знать не нужно.
    @AppStorage("sufler.archiveOn") private var archiveOn = true
    // чат встроен в суфлёр (панель справа); видимость запоминается
    @AppStorage("sufler.showChat") private var showChat = false
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
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
            // Вне встречи поле вопроса живёт только ради архива: тумблер
            // «Архив» выключен — прячем и поле, и разделитель. При ОТКРЫТОМ
            // чате вне встречи бар тоже прячем: два похожих поля ввода на
            // одном экране читались как дубль («почему два окна запросов?»),
            // а чат с памятью закрывает те же вопросы. Во время встречи бар
            // нужен всегда — это вопрос демону по живой стенограмме.
            if sufler.isRunning || (archiveOn && !showChat) {
                Divider()
                askBar
            }
        }
        // 1080 при открытом чате: сплиту нужно ≥620 (минимумы панелей) плюс
        // 430 чата — узкое окно само дорастает, и никто никого не перекрывает
        .frame(minWidth: showChat ? 1080 : 700, minHeight: 460)
        .onDisappear { if sufler.isRunning { sufler.stop() } }
        .sheet(isPresented: $showFirstRun) {
            FirstRunView { sufler.start() }
        }
        .onAppear {
            // Первый запуск: объясняем, что это и зачем микрофон, ДО того как
            // человек нажмёт «Слушать встречу» и получит системный запрос.
            if !firstRunSeen { showFirstRun = true }
        }
    }

    // Вопрос работает ВСЕГДА, а не только во время встречи.
    //
    // Раньше кнопка блокировалась при остановленном демоне — и самый частый
    // вопрос («что обсуждали вчера?») было физически не задать: ради ответа про
    // вчерашнюю встречу приходилось запускать запись сегодняшней. Теперь идёт
    // живая встреча — спрашиваем демона (он видит стенограмму), не идёт —
    // ищем по архиву встреч и графу через Чароит.
    private var askBar: some View {
        HStack(spacing: 10) {
            Image(systemName: "questionmark.bubble")
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)   // декоративная, рядом есть поле с подписью
            TextField(sufler.isRunning
                      ? "Спросить по этой встрече и графу…"
                      : "Что обсуждали на встрече вчера?  ·  спросить по архиву встреч",
                      text: $question)
                .textFieldStyle(.plain)
                .onSubmit { submitQuestion() }
            DictationButton(text: $question)
            if isSearchingArchive {
                ProgressView().controlSize(.small)
            }
            // Подготовка ко встрече: та же архивная механика, но бриф-формат
            // (статус, решено, открыто, люди) вместо ответа на вопрос.
            if !sufler.isRunning {
                Button("К встрече") { submitBrief() }
                    .disabled(question.trimmingCharacters(in: .whitespaces).isEmpty || isSearchingArchive)
                    .help("Бриф для подготовки: статус темы, что решено, что открыто, кто вовлечён")
            }
            Button("Спросить") { submitQuestion() }
                .buttonStyle(.borderedProminent)
                .tint(Color(hex: "#6366F1"))
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
        isSearchingArchive = true
        archiveAnswer = ""
        Task {
            defer { Task { @MainActor in isSearchingArchive = false } }
            // 1200 знаков на файл: модель отвечает по содержимому, а не по
            // обрезкам (на коротких сниппетах честно пишет «информации нет»)
            let found = ArchiveSearch.search(query: q, limit: 5, snippet: 1200)
            guard !found.isEmpty else {
                await MainActor.run {
                    archiveAnswer = "В графе ничего не нашлось по запросу. "
                        + "Проверь путь установки в Настройках (graph_dir в config.yaml)."
                }
                return
            }
            // компактный список источников: строки выдачи «• путь/файл.md»
            let sources = found.split(separator: "\n")
                .filter { $0.hasPrefix("• ") }
                .map { String($0.dropFirst(2)).replacingOccurrences(of: ".md", with: "") }
            let sourceBlock = sources.isEmpty ? "" : "\n\nИсточники:\n"
                + sources.map { "· \($0)" }.joined(separator: "\n")
            // прогресс: человек видит, ЧТО нашлось, но не сырые куски
            await MainActor.run {
                archiveAnswer = "Нашёл источников: \(sources.count) — формулирую ответ…" + sourceBlock
            }
            let instruction = brief
                ? "Готовлюсь к встрече по теме: \(q)\n\nФрагменты из архива встреч:\n\(found)\n\n"
                    + "Собери бриф для подготовки, по-русски, телеграфно, строго блоками. "
                    + "«Статус:» — 1-2 строки, где тема сейчас. "
                    + "«Решено:» — пункты «— дата: что» от старого к новому. "
                    + "«Открыто:» — нерешённые вопросы и риски, пункты. "
                    + "«Люди:» — кто вовлечён и чем занят, из фрагментов, одной строкой на "
                    + "человека; никого нет — блок не пиши. Только факты из фрагментов, "
                    + "ничего не выдумывай, без вступлений и воды. Если фрагменты не про "
                    + "эту тему — одна строка: по теме в архиве пусто."
                : "Вопрос: \(q)\n\nФрагменты из архива встреч:\n\(found)\n\n"
                    + "Составь ответ строго такой структуры, по-русски, телеграфно. "
                    + "Первая строка — прямой ответ на вопрос, 1-2 предложения, без "
                    + "нумерации и префиксов. Затем блок «Факты:» — пункты вида "
                    + "«— дата: что решили/что случилось (кто)», хронологически от "
                    + "старого к новому; даты бери из фрагментов. Если по фрагментам "
                    + "что-то осталось нерешённым — блок «Открыто:» с пунктами, иначе "
                    + "его не пиши. Только факты из фрагментов, ничего не выдумывай. "
                    + "Без вступлений, без воды, без markdown-заголовков. Если ответа "
                    + "во фрагментах нет — одна строка: чего именно не хватает."
            let answer = await ArchiveSearch.ask(
                question: instruction,
                system: "Ты — ассистент по архиву рабочих встреч. "
                    + "Отвечаешь только по приведённым фрагментам, без домыслов, телеграфно.",
                model: LocalChatService.shared.model,
                ollama: AppSettings.ollamaURL)
            let trimmed = answer.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else {
                // синтез не удался — хотя бы сырьё, лучше каша, чем пустота
                await MainActor.run { archiveAnswer = found }
                return
            }
            await MainActor.run { archiveAnswer = trimmed + sourceBlock }
        }
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
                w.identifier?.rawValue.contains("localchat") == true || w.title == "Локальный чат"
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
                candidates.append(v.appendingPathComponent("Встречи-архив"))
                candidates.append(v.appendingPathComponent("Встречи"))
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
        alert.messageText = "Папка со встречами пока не создана"
        alert.informativeText = "Она появится после первой записанной встречи — "
            + "нажмите «Слушать встречу»."
        alert.addButton(withTitle: "Понятно")
        alert.runModal()
    }

    /// Статус про сбой, а не про обычный ход дела.
    private var statusIsProblem: Bool {
        let s = sufler.status
        return s.hasPrefix("⛔️") || s.contains("прервалась")
            || s.contains("замерла") || s.contains("Не удалось")
    }

    /// Что показывать в панели: во время встречи — подсказку демона, вне
    /// встречи — ответ по архиву. Пусто — приглашение спросить.
    private var paneText: AttributedString {
        if sufler.isRunning {
            return sufler.hint.isEmpty
                ? AttributedString("⌘⏎ — подсказка по последним минутам")
                : withBoldQuestions(sufler.hint)
        }
        if archiveAnswer.isEmpty {
            // при открытом чате нижнего поля нет — не отправляем в никуда
            return AttributedString(showChat
                ? "Ответы по архиву появятся здесь. Спросить — в чате справа или закрой Чат для поля внизу"
                : "Спроси про прошлые встречи в поле внизу — найду по архиву и графу")
        }
        return withBoldQuestions(archiveAnswer)
    }

    private var paneIsPlaceholder: Bool {
        sufler.isRunning ? sufler.hint.isEmpty : archiveAnswer.isEmpty
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
            var piece = AttributedString(line)
            if line.trimmingCharacters(in: .whitespaces).hasPrefix("❓") {
                piece.font = .callout.bold()
            }
            out.append(piece)
        }
        return out
    }

    // MARK: - Header

    private var header: some View {
        HStack(spacing: 12) {
            Button {
                sufler.isRunning ? sufler.stop() : sufler.start()
            } label: {
                Label {
                    Text(sufler.isRunning ? "Стоп" : "Слушать встречу")
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
                    Capsule().fill(sufler.isRunning ? Color.red : Color(hex: "#6366F1"))
                )
            }
            .buttonStyle(.plain)
            .keyboardShortcut(.space, modifiers: [.command, .shift])

            // Сбой записи нельзя показывать так же, как «Готов к запуску»:
            // мелкий серый текст в одну строку человек на встрече не заметит, а
            // сообщение «нажмите ещё раз» вдобавок обрезалось на полуслове.
            Text(sufler.status)
                .font(statusIsProblem ? .caption.weight(.medium) : .caption)
                .foregroundStyle(statusIsProblem ? Color.red : Color.secondary)
                .lineLimit(statusIsProblem ? 2 : 1)
                .fixedSize(horizontal: false, vertical: statusIsProblem)
                .textSelection(.enabled)

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
            HStack(spacing: 12) {
                Toggle(isOn: $sufler.hintsOn) { Text("Подсказки").fixedSize() }
                    .help("Подсказки и мгновенные ответы на вопросы собеседника")
                    .accessibilityLabel("Подсказки во время встречи")
                    .accessibilityHint("Мгновенные ответы на вопросы собеседника")
                Toggle(isOn: $sufler.thesesOn) { Text("Тезисы").fixedSize() }
                    .help("Автотезисы 📌💎💭 и дежавю ⏮ по ходу встречи")
                    .accessibilityLabel("Автотезисы")
                    .accessibilityHint("Ключевые мысли и повторы по ходу встречи")
                Toggle(isOn: $sufler.cloudOn) { Text("Claude").fixedSize() }
                    .help("Параллельные ответы Claude на вопросы собеседника")
                    .accessibilityLabel("Ответы Claude")
                    .accessibilityHint("Параллельные ответы облачной модели")
                Toggle(isOn: $archiveOn) { Text("Архив").fixedSize() }
                    .help("Вопросы по архиву встреч и графу, когда встреча не идёт")
                    .accessibilityLabel("Поиск по архиву встреч")
                    .accessibilityHint("Ответы по прошлым встречам вне записи")
            }
            .fixedSize(horizontal: true, vertical: false)
            .toggleStyle(.switch)
            .controlSize(.mini)
            .font(.caption)

            Button {
                showChat.toggle()
            } label: {
                Label("Чат", systemImage: showChat ? "message.fill" : "message")
            }
            .help("Локальный чат с памятью — панель прямо в окне суфлёра")

            Button {
                openChatWindow()  // не просто открыть, а вынести вперёд
            } label: {
                Image(systemName: "arrow.up.forward.square")
            }
            .help("Чат отдельным окном (история общая с панелью)")

            Button {
                openMeetingsFolder()
            } label: {
                Label("Встречи", systemImage: "folder")
            }
            .help("Открыть встречи в Finder (стенограммы, тезисы, минутки, разборы)")

            // Действия записи живут ТОЛЬКО во время встречи: вне её это были
            // три вечно серые кнопки, занимавшие треть тулбара. Появляются
            // мягко вместе со стартом записи.
            if sufler.isRunning {
                Button("Подсказка") { sufler.requestHint() }
                    .keyboardShortcut(.return, modifiers: .command)
                    .disabled(sufler.isHinting)
                    .help("Подсказка по последним минутам (⌘⏎)")

                Button("Claude") { sufler.requestCloud() }
                    .keyboardShortcut(.return, modifiers: [.command, .shift])
                    .disabled(sufler.isClouding)
                    .help("Спросить Claude по ходу встречи (⌘⇧⏎)")

                Button("Протокол") { sufler.requestSummary() }
                    .disabled(sufler.isHinting)
                    .help("Собрать протокол встречи прямо сейчас")
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .animation(.easeInOut(duration: 0.2), value: sufler.isRunning)
    }

    // MARK: - Левая панель: стенограмма

    private var transcriptPane: some View {
        VStack(alignment: .leading, spacing: 0) {
            paneTitle("Стенограмма", systemImage: "text.quote")
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        if sufler.lines.isEmpty {
                            emptyState(sufler.isRunning ? "waveform" : "waveform.circle",
                                       sufler.isRunning ? "Слушаю…" : "Нажми «Слушать встречу»")
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

    // Выключенная плашка убирает и свою панель: ⚡ — «Подсказка», Тезисы —
    // «Тезисы», ☁️ — «Claude». Пустых мёртвых панелей на экране нет.
    private var rightPane: some View {
        VSplitView {
            if sufler.thesesOn {
            VStack(alignment: .leading, spacing: 0) {
                paneTitle("Тезисы", systemImage: "list.bullet.rectangle")
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 6) {
                            if sufler.theses.isEmpty {
                                emptyState("list.bullet.rectangle",
                                           "Автотезисы появятся по ходу встречи")
                            }
                            ForEach(Array(sufler.theses.enumerated()), id: \.offset) { _, t in
                                thesisCard(t)
                                    .transition(.opacity.combined(with: .move(edge: .bottom)))
                            }
                            Color.clear.frame(height: 1).id("thesesBottom")
                        }
                        .padding(10)
                        .animation(.easeOut(duration: 0.25), value: sufler.theses.count)
                    }
                    .onChange(of: sufler.theses.count) { _, n in
                        guard n > 0 else { return }
                        // после лейаута, иначе scrollTo промахивается по свежему элементу
                        DispatchQueue.main.async {
                            withAnimation(.easeOut(duration: 0.15)) {
                                proxy.scrollTo("thesesBottom", anchor: .bottom)
                            }
                        }
                    }
                }
            }
            .frame(minHeight: 140)
            }

            // Вне встречи панель живёт для ответов по архиву (тумблер «Архив»),
            // во время встречи — для подсказок (тумблер «Подсказки»). С обоими
            // выключенными пустых мёртвых панелей на экране нет.
            if sufler.isRunning ? sufler.hintsOn : archiveOn {
            VStack(alignment: .leading, spacing: 0) {
                HStack {
                    paneTitle(sufler.isRunning ? "Подсказка" : "Ответ по архиву",
                              systemImage: sufler.isRunning ? "lightbulb" : "clock.arrow.circlepath")
                    if sufler.isHinting || isSearchingArchive {
                        ProgressView().controlSize(.small).padding(.trailing, 10)
                    }
                }
                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(alignment: .leading, spacing: 0) {
                            Color.clear.frame(height: 1).id("hintTop")
                            Text(paneText)
                                .font(.callout)
                                .foregroundStyle(paneIsPlaceholder ? .tertiary : .primary)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
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
            .background(Color(hex: "#6366F1").opacity(0.05))
            }

            if sufler.cloudOn {
            VStack(alignment: .leading, spacing: 0) {
                HStack {
                    paneTitle("Claude", systemImage: "cloud.fill")
                    if sufler.isClouding {
                        ProgressView().controlSize(.small).padding(.trailing, 10)
                    }
                }
                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(alignment: .leading, spacing: 0) {
                            Text(sufler.cloud.isEmpty
                                 ? AttributedString("Вопрос собеседника уйдёт Claude автоматически · ⌘⇧⏎ — вручную")
                                 : withBoldQuestions(sufler.cloud))
                                .font(.callout)
                                .foregroundStyle(sufler.cloud.isEmpty ? .tertiary : .primary)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                            Color.clear.frame(height: 1).id("cloudBottom")
                        }
                        .padding(12)
                    }
                    .onChange(of: sufler.cloud) { _, _ in
                        DispatchQueue.main.async {
                            proxy.scrollTo("cloudBottom", anchor: .bottom)
                        }
                    }
                }
            }
            .frame(minHeight: 120)
            .background(Color(hex: "#0EA5E9").opacity(0.06))
            }

            if !sufler.thesesOn && !sufler.hintsOn && !sufler.cloudOn {
                Text("Все панели выключены — включи плашки сверху")
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
                .foregroundStyle(Color(hex: "#6366F1"))
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

    private func paneTitle(_ title: String, systemImage: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: systemImage)
                .font(.caption)
            Text(title)
                .font(.caption.weight(.semibold))
            Spacer()
        }
        .foregroundStyle(.secondary)
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(.bar)
    }

    /// Единое пустое состояние панелей: иконка + строка, спокойно и тепло.
    private func emptyState(_ symbol: String, _ text: String) -> some View {
        VStack(spacing: 8) {
            Image(systemName: symbol)
                .font(.title2)
                .foregroundStyle(.quaternary)
            Text(text)
                .font(.caption)
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 28)
        .padding(.horizontal, 16)
    }

    /// Тезис — карточка с подложкой по типу: 📌 решения тёплые, 💎 факты
    /// индиго, ⏮ контекст из архива бирюзовый, 💭/🔬 мысли нейтральные.
    /// Плоский текст с эмодзи читался как лог; карточки дают глазу зацепки.
    private func thesisCard(_ t: String) -> some View {
        let tint: Color = t.hasPrefix("📌") ? .orange
            : t.hasPrefix("💎") ? Color(hex: "#6366F1")
            : t.hasPrefix("⏮") ? .teal
            : .gray
        return Text(t)
            .font(.callout)
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(tint.opacity(0.08), in: RoundedRectangle(cornerRadius: 7, style: .continuous))
            .overlay(alignment: .leading) {
                RoundedRectangle(cornerRadius: 1.5)
                    .fill(tint.opacity(0.55))
                    .frame(width: 3)
                    .padding(.vertical, 5)
            }
    }
}

#endif
