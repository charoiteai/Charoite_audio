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
    @State private var question = ""
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
            CalendarService.shared.recording(sufler.isRunning)
            // Dev-хуки скринов/смоков: на живой машине владельца клавиатурный
            // ввод в чужое окно проигрывает гонку за фокус — вопрос и окна
            // задаются окружением и выполняются сами.
            let env = ProcessInfo.processInfo.environment
            if let q = env["CHAROITE_ASK"], !q.isEmpty {
                // архивный контур суфлёра вычищен (№22): вопрос уходит в
                // живой раздел «Память» — тот же ответ по графу и архиву
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                    navigation.open(.memory)
                    LocalChatService.shared.send(q)
                }
            }
            if env["CHAROITE_OPEN_TASKS"] == "1" {
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { navigation.open(.tasks) }
            }
        }
    }

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
                .charoite(.prominent)
                Button(L.t("Не сейчас", "Not now", "暂不")) {
                    CalendarService.shared.dismissCue()
                }
                .charoite(.quiet)
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
            TextField(L.t("Спросить по этой встрече и графу…",
                          "Ask about this meeting and the graph…",
                          "就本次会议和图谱提问…"),
                      text: $question)
                .textFieldStyle(.plain)
                .onSubmit { submitQuestion() }
            DictationButton(text: $question)
            Button(L.t("Спросить", "Ask", "提问")) { submitQuestion() }
                .charoite(.prominent)
                // при идущей подсказке вопрос отбрасывает guard в ask
                // (демон-то принял бы, отказ живёт на стороне приложения),
                // а поле уже очистилось — текст терялся (ревью 16.08)
                .disabled(question.trimmingCharacters(in: .whitespaces).isEmpty
                          || sufler.isHinting)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }

    private func submitQuestion() {
        let q = question.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty, sufler.isRunning, !sufler.isHinting else { return }
        question = ""
        sufler.ask(q)
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
        if sufler.isRunning {
            // Подтверждение Stop требует немедленного второго действия.
            if sufler.stopConfirmPending { return sufler.status }
            // Отказ диска важнее вспомогательного слоя; capture/restart error
            // важнее нагрузки; lag/stall важнее обычной служебной строки.
            if sufler.pipelineStatusIsCritical,
               let pipelineStatus = sufler.pipelineStatusText {
                // Демон шлёт error-статус с ПРИЧИНОЙ отказа («ЗАПИСЬ НА ДИСК
                // ВЫКЛЮЧЕНА: <исключение>»); генерик-баннер поверх него
                // прятал её до конца встречи (круг-1 GLM, I2). Пока причина
                // на экране — показываем её; затёрло обычным статусом —
                // возвращается персистентный баннер. Уступаем ТОЛЬКО ошибке
                // самого демона: Swift-`fail()` (таймаут подсказки) при
                // мёртвом STT больше никогда не сменится статусом и прятал
                // бы критикал до конца встречи (круг-2 DS, I1).
                return sufler.statusErrorFromDaemon ? sufler.status : pipelineStatus
            }
            if sufler.statusIsError { return sufler.status }
            if let pipelineStatus = sufler.pipelineStatusText {
                return pipelineStatus
            }
        }
        if !sufler.isRunning, let processingStatus = processing.statusText {
            return processingStatus
        }
        return sufler.status
    }

    private var statusIsProblem: Bool {
        if sufler.isRunning {
            if sufler.stopConfirmPending { return false }
            return sufler.pipelineStatusText != nil || sufler.statusIsError
        }
        if !sufler.isRunning, processing.statusText != nil {
            return processing.isError
        }
        return sufler.statusIsError
    }

    private var statusColor: Color {
        if sufler.isRunning {
            if sufler.stopConfirmPending { return .secondary }
            if sufler.pipelineStatusIsCritical || sufler.statusIsError { return .red }
            if sufler.pipelineStatusText != nil { return Theme.warning }
        }
        return statusIsProblem ? .red : .secondary
    }

    /// Откуда панель берёт текст прямо сейчас.
    /// Политика правой панели — стопка, не взаимоисключение (ревью 16.08):
    /// подсказка живёт КАРТОЧКОЙ над нитью и гаснет со следующим обновлением
    /// нити, а нить видна всегда, пока в ней есть текст, — и во время
    /// встречи, и после «Стоп» (регрессия #255: обёртка if isRunning прятала
    /// итог встречи, а одна пришедшая подсказка закрывала нить навсегда).
    struct PaneStack: Equatable {
        let showHintCard: Bool
        let showThread: Bool
        let placeholder: String?
    }

    static func paneStack(hasHint: Bool, hinting: Bool, hasThread: Bool,
                          running: Bool, hintsOn: Bool = true) -> PaneStack {
        let hint = hasHint || hinting
        let thread = hasThread
        var placeholder: String?
        if !hint && !thread {
            if running && !hintsOn {
                // Нить растёт только под тумблером подсказок (демон:
                // toggles["hints"]) — с выключенным обещание «появится через
                // минуту» было бы враньём. Тезисный контур из панели убран
                // (пакет владельца 24.08), его тумблер больше не участвует.
                placeholder = L.t(
                    "Подсказки выключены — нить не растёт. Включи плашку сверху.",
                    "Hints are off — the thread won't grow. Turn the chip on above.",
                    "提示已关闭——脉络不会生长。请打开上方开关。")
            } else if running {
                placeholder = L.t(
                    "Нить встречи появится через минуту разговора · ⌘⏎ — подсказка сейчас",
                    "The meeting thread appears after a minute of talk · ⌘⏎ — hint now",
                    "会议脉络将在交谈一分钟后出现 · ⌘⏎ — 立即提示")
            } else {
                placeholder = L.t(
                    "Нить прошлой встречи останется здесь после «Стоп». Вопросы по архиву — в разделе «Память».",
                    "The last meeting's thread stays here after Stop. Archive questions live in Memory.",
                    "上一场会议的脉络会在停止后保留在这里。档案问题请前往「记忆」。")
            }
        }
        return PaneStack(showHintCard: hint, showThread: thread,
                         placeholder: placeholder)
    }

    private var pane: PaneStack {
        Self.paneStack(hasHint: !sufler.hint.isEmpty,
                       hinting: sufler.isHinting,
                       hasThread: !sufler.thread.isEmpty,
                       running: sufler.isRunning,
                       hintsOn: sufler.hintsOn)
    }

    /// ==Фрагменты==, которые внесла облачная ревизия нити, — небесным фоном:
    /// правка видна в самой строке, отдельного блока «☁️ уточнения» больше нет.
    /// Небесный цвет — общий знак «это ходило в облако» (docs/DESIGN.md).
    private func cloudMarked(_ line: String) -> AttributedString {
        guard line.contains("==") else { return AttributedString(line) }
        var out = AttributedString()
        let parts = line.components(separatedBy: "==")
        for (i, part) in parts.enumerated() {
            if part.isEmpty { continue }
            var piece = AttributedString(part)
            if i % 2 == 1 {
                piece.backgroundColor = Theme.sky.opacity(0.16)
                piece.foregroundColor = Theme.sky
            }
            out.append(piece)
        }
        return out
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
            var piece = cloudMarked(line)
            if trimmed.hasPrefix("●") {
                piece.font = .callout.bold()
            } else if trimmed.hasPrefix("⚑") || trimmed.hasPrefix("📌") {
                piece.font = .callout.weight(.medium)
                piece.foregroundColor = Theme.warning
            } else if trimmed.hasPrefix("⚡") {
                // Ответ без строки вопроса (пакет 24.08): сама строка и есть
                // подсказка — полужирно и цветом действия, чтобы находилась
                // боковым зрением.
                piece.font = .callout.weight(.semibold)
                piece.foregroundColor = Theme.accent
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
        // Тулбар шире окна не должен ТЕРЯТЬ кнопки: сегмент действий встречи
        // уезжал за правый край — «включаешь, а кнопок не видно». Скролл
        // делает переполнение видимым, а не обрезанным.
        ScrollView(.horizontal, showsIndicators: false) {
            headerRow
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
        }
        .animation(.easeInOut(duration: 0.2), value: sufler.isRunning)
    }

    private var headerRow: some View {
        HStack(spacing: 12) {
            Button {
                sufler.toggle()
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
                // не переносить: в тесном тулбаре главная кнопка ломалась
                // в «Слу-шать встр ечу» на три строки
                .fixedSize()
                .foregroundStyle(.white)
                // Форма и высота от шкалы (32 pt, радиус 7), а не капсула:
                // в одном ряду с кнопками шкалы капсула читалась как чужая.
                // Заливка ручная — «идёт запись» ролью шкалы не описывается.
                .frame(height: 32)
                .padding(.horizontal, 16)
                .background(
                    RoundedRectangle(cornerRadius: 7, style: .continuous)
                        .fill(sufler.isRunning
                              ? AnyShapeStyle(Color.red)
                              : AnyShapeStyle(Theme.brand))
                        .shadow(color: Theme.accent.opacity(sufler.isRunning ? 0 : 0.4),
                                radius: 8, y: 3)
                )
            }
            .buttonStyle(.plain)
            .disabled(sufler.isTransitioning)
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
                .foregroundStyle(statusColor)
                .lineLimit(statusIsProblem ? 2 : 1)
                .fixedSize(horizontal: false, vertical: statusIsProblem)
                .textSelection(.enabled)

            if !sufler.isRunning, processing.isProcessing {
                ProgressView().controlSize(.small)
            }
            if !sufler.isRunning, let actionTitle = processing.actionTitle {
                Button(actionTitle) { processing.openResult() }
                    .charoite(.regular, .m)
            }
            // Ошибка — не тупик: стенограмма цела, конвейер перезапускаем
            // отсюда же. Раньше повтор существовал лишь как скрипт в терминале.
            if !sufler.isRunning, processing.canRetry || processing.retryInFlight {
                Button(L.t("Повторить обработку", "Retry processing", "重新处理")) {
                    processing.retry()
                }
                .charoite(.regular, .m)
                // пока прошлый повтор жив — кнопка гаснет, но остаётся на
                // месте: исчезающая кнопка под курсором читается как сбой,
                // а два конвейера на одну встречу пишут один статус и лог
                .disabled(processing.retryInFlight)
            }

            // Действия встречи — сразу за кнопкой записи, ДО навигации: во
            // время записи это главные кнопки и они обязаны быть видны первыми.
            if sufler.isRunning {
                CharoiteSegment {
                    // «Конспект», не «Подсказка»: рядом в LayerBar живёт чип
                    // «Подсказки», и две одинаковые надписи читались как дубль
                    // (слова владельца 24.08: «почему сверху две кнопки подсказки»).
                    Button(L.t("Конспект", "Digest", "摘要")) { sufler.requestHint() }
                        .charoite(.quiet, .m)
                        .keyboardShortcut(.return, modifiers: .command)
                        .disabled(sufler.isHinting)
                        .help(L.t("Конспект последних минут разговора (⌘⏎)", "A digest of the last minutes (⌘⏎)", "最近几分钟的摘要（⌘⏎）"))

                    Button("Claude") { sufler.requestCloud() }
                        .charoite(.quiet, .m)
                        .keyboardShortcut(.return, modifiers: [.command, .shift])
                        .disabled(sufler.isClouding || !sufler.cloudOn)
                        .help(sufler.cloudOn
                              ? L.t("Спросить Claude по ходу встречи — кусок стенограммы уйдёт в облако (⌘⇧⏎)", "Ask Claude mid-meeting — a transcript slice goes to the cloud (⌘⇧⏎)", "会议中问 Claude — 一段逐字稿将发送至云端（⌘⇧⏎）")
                              : L.t("Облако выключено: включите «Claude» в тулбаре. Стенограмма не покидает машину", "Cloud is off: enable “Claude” in the toolbar. The transcript never leaves this machine", "云端已关闭：在工具栏开启「Claude」。逐字稿不会离开本机"))

                    Button(L.t("Протокол", "Minutes", "纪要")) { sufler.requestSummary() }
                        .charoite(.quiet, .m)
                        .disabled(sufler.isHinting)
                        .help(L.t("Собрать протокол встречи прямо сейчас", "Build the meeting minutes right now", "立即生成会议纪要"))
                }
            }

            // Живые тумблеры: выключил на этой встрече — станет дефолтом следующих.
            // Подписи текстом, а не эмодзи: «⚡» и «☁️» в Toggle(.switch) на маке
            // не рисуются — оставались три одинаковых переключателя без подписей.
            // accessibilityLabel обязателен отдельно от видимой подписи: VoiceOver
            // читает Toggle как безымянный «флажок», текст рядом в озвучку не
            // попадает (проверено обходом AX-дерева). fixedSize обязателен: в
            // узкой панели SwiftUI ломал подписи по буквам — «По-дс-ка-зки» в
            // четыре строки. Чипы вместо системных свитчей (UI_REVISION_2026-08,
            // правило 1): свитчи красили полосу системным синим и спорили с
            // фирменной кнопкой. Чип несёт цвет слоя: индиго — локальное,
            // sky — единственный слой, который уходит с машины.
            LayerBar {
                LayerChip(title: L.t("Подсказки", "Hints", "提示"), isOn: $sufler.hintsOn)
                    .help(L.t("Подсказки и мгновенные ответы на вопросы собеседника", "Hints and instant answers to the other side's questions", "提示与对方提问的即时回答"))
                    .accessibilityLabel(L.t("Подсказки во время встречи", "Hints during the meeting", "会议期间的提示"))
                    .accessibilityHint(L.t("Мгновенные ответы на вопросы собеседника", "Instant answers to the other side's questions", "对方提问的即时回答"))
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
            .charoite(.regular, .m)
            .help(L.t("Локальный чат с памятью — панель прямо в окне суфлёра", "Local chat with memory — a pane right in the copilot window", "带记忆的本地聊天——直接嵌在提词窗口"))

            Button {
                openChatWindow()  // не просто открыть, а вынести вперёд
            } label: {
                Image(systemName: "arrow.up.forward.square")
            }
            .charoite(.icon, .m)
            .help(L.t("Чат отдельным окном (история общая с панелью)", "Chat in its own window (history shared with the pane)", "聊天独立窗口(与面板共用历史)"))

            // Результат вчерашней записи не должен исчезать в секунду, когда
            // начата новая.
            if !processing.history.isEmpty {
                Button {
                    navigation.open(.meetings)
                } label: {
                    Label(L.t("Встречи", "Meetings", "会议"),
                          systemImage: "clock.arrow.circlepath")
                }
                .charoite(.regular, .m)
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
            .charoite(.regular, .m)
            .help(L.t("Поручения со встреч (чекбоксы из минуток и заметок графа)", "Meeting action items (checkboxes from minutes and graph notes)", "会议行动项（来自纪要和图谱笔记的复选框）"))

            Button {
                openMeetingsFolder()
            } label: {
                Label(L.t("Встречи", "Meetings", "会议"), systemImage: "folder")
            }
            .charoite(.regular, .m)
            .help(L.t("Открыть встречи в Finder (стенограммы, тезисы, минутки, разборы)", "Open meetings in Finder (transcripts, theses, minutes, debriefs)", "在 Finder 打开会议（逐字稿、要点、纪要、复盘）"))

        }
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
                                             running: sufler.isRunning)
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

    // MARK: - Правая панель: нить + подсказка

    // Выключенная плашка убирает своё: ⚡ — карточку подсказки, ☁️ —
    // облачную ленту (тезисный контур из панели убран, пакет владельца 24.08). Подсказка и Claude живут в ОДНОЙ
    // панели (решение 28.07): локальная карточка сверху, облачные ответы
    // ниже sky-карточкой — граница «что ушло с машины» видна цветом,
    // а не отдельным окном. Пустых мёртвых панелей на экране нет.
    private var rightPane: some View {
        // Панель НЕ привязана к isRunning: «Стоп» не смеет стирать итог
        // встречи с экрана (регрессия #255 — второй раз). Архивные вопросы
        // живут в разделе «Память», здесь — только живой разговор и его нить.
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                paneTitle(L.t("Нить встречи", "Meeting thread", "会议脉络"),
                          systemImage: "text.line.first.and.arrowtriangle.forward",
                          copy: { sufler.thread })
                if sufler.isHinting || (sufler.isRunning && sufler.isClouding) {
                    ProgressView().controlSize(.small).padding(.trailing, 10)
                }
            }
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        Color.clear.frame(height: 1).id("hintTop")
                        // Подсказка — карточкой НАД нитью, а не вместо неё:
                        // прежний взаимоисключающий выбор давал одной
                        // подсказке закрыть нить до конца встречи. Авто-бриф
                        // гаснет со следующим обновлением нити, ручной ответ —
                        // только крестиком (сервис, hintIsManual).
                        if pane.showHintCard {
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(L.t("Подсказка", "Hint", "提示"))
                                        .font(.caption2.weight(.semibold))
                                        .foregroundStyle(.secondary)
                                    Spacer()
                                    // Ручной ответ нить не гасит (см. сервис) —
                                    // убрать карточку может только человек.
                                    // Во время живого стрима крестик спрятан:
                                    // клик посреди авто-стрима чистил буфер, а
                                    // следующий токен воскрешал карточку (п.3
                                    // ревью 16.08) — no-op не предлагаем.
                                    if !sufler.isHinting && !sufler.isAutoHinting {
                                        Button {
                                            sufler.dismissHint()
                                        } label: {
                                            Image(systemName: "xmark")
                                                .font(.caption2.weight(.semibold))
                                                .foregroundStyle(.secondary)
                                        }
                                        .buttonStyle(.plain)
                                        .help(L.t("Убрать подсказку", "Dismiss hint", "关闭提示"))
                                    }
                                }
                                Text(withBoldQuestions(sufler.hint))
                                    .font(.callout)
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .padding(10)
                            .background(RoundedRectangle(cornerRadius: Theme.radius)
                                .fill(Theme.accent.opacity(0.07)))
                            .padding(.bottom, 10)
                        }
                        if pane.showThread {
                            Text(withThreadMarks(sufler.thread))
                                .font(.callout)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        } else if let placeholder = pane.placeholder {
                            Text(placeholder)
                                .font(.callout)
                                .foregroundStyle(.tertiary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        // облачная лента — в той же панели, sky-карточкой:
                        // видно, ЧТО ушло с машины, без отдельного окна
                        if sufler.isRunning && sufler.cloudOn {
                            cloudCard
                                .padding(.top, 10)
                        }
                        Color.clear.frame(height: 1).id("hintBottom")
                    }
                    .padding(12)
                }
                // Стрим токенов, без анимации — иначе дёргается. Подсказка
                // теперь КАРТОЧКА НАД нитью: держаться за низ панели значит
                // мотать читателя мимо неё — при видимой карточке держимся
                // за её верх (ревью 16.08, №22).
                .onChange(of: sufler.hint) { _, _ in
                    DispatchQueue.main.async {
                        proxy.scrollTo(pane.showHintCard ? "hintTop" : "hintBottom",
                                       anchor: pane.showHintCard ? .top : .bottom)
                    }
                }
                // лента Claude растёт вниз — держимся за низ и для неё
                .onChange(of: sufler.cloud) { _, _ in
                    DispatchQueue.main.async {
                        proxy.scrollTo("hintBottom", anchor: .bottom)
                    }
                }
            }
        }
        .frame(minHeight: 140)
        // Ответы по архиву и графу — лавандовая поверхность памяти
        // (токен, а не accent.opacity по месту: дизайн-аудит 21.08).
        .background(Theme.surfaceMemory)
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
        CloudSurface {
            HStack(spacing: 6) {
                Image(systemName: "cloud.fill")
                    .font(.caption2)
                Text("Claude")
                    .font(.caption2.weight(.semibold))
                // Единственный слой, который покидает машину, — сказано
                // словами рядом с цветом: цвет не должен быть единственным
                // сигналом (DESIGN.md, доступность).
                Text("· " + L.t("уходит с машины", "leaves this Mac", "离开本机"))
                    .font(.caption2)
                    .foregroundStyle(Theme.sky.opacity(0.8))
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
