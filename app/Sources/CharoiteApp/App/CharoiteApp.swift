import CoreSpotlight
import SwiftUI

#if os(macOS)

@main
struct CharoiteApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        WindowGroup(L.t("Чароит", "Charoite", "Charoite"), id: "main") {
            WorkspaceView()
                // Фирменный тинт вместо системного синего: кольцо фокуса,
                // прогрессы и оставшиеся системные контролы — индиго.
                .tint(Theme.accent)
                // Клик по встрече в Spotlight: системная активность несёт
                // uniqueIdentifier — это meetingID из индекса.
                .onContinueUserActivity(CSSearchableItemActionType) { activity in
                    guard let id = activity.userInfo?[CSSearchableItemActivityIdentifier] as? String
                    else { return }
                    WorkspaceNavigation.shared.open(.meeting, meetingID: id)
                }
        }
        .defaultSize(width: 1180, height: 760)

        Window(L.t("Чат с памятью", "Chat with memory", "记忆聊天"), id: "localchat") {
            LocalChatView()
                .tint(Theme.accent)
                .frame(minWidth: 520, minHeight: 420)   // минимум только у окна: в панели вью уже
        }
        .defaultSize(width: 640, height: 520)

        MenuBarExtra {
            MenuBarView()
                .tint(Theme.accent)
        } label: {
            Image(systemName: "brain.head.profile")
        }
        .menuBarExtraStyle(.window)

        Settings {
            SettingsView()
                .tint(Theme.accent)
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    /// Настройки переезжают за приложением при смене идентификатора.
    ///
    /// UserDefaults живут в домене bundle id, и переименование обнуляло всё
    /// разом: путь установки, адрес Ollama, тумблеры контуров, отметку
    /// онбординга. Это уже случалось при переходе с прежнего идентификатора —
    /// приложение стартовало с пустыми настройками, и человек видел
    /// «первый запуск» вместо своей рабочей конфигурации.
    static func migrateSettingsFromOldBundle() {
        let d = UserDefaults.standard
        guard d.object(forKey: "charoite.root") == nil else { return }  // уже настроено
        // Домены прежних идентификаторов приложения. Своих внутренних сюда
        // не добавляем — публичной сборке они ни к чему.
        for old in ["ai.charoite.sufler"] {
            guard let src = UserDefaults(suiteName: old) else { continue }
            let moved = migrateSettings(from: src.dictionaryRepresentation(), into: d)
            guard moved > 0 else { continue }
            NSLog("Charoite: перенесено настроек из %@: %d", old, moved)
            return
        }
    }

    /// Перенос ключей из старого домена. Возвращает число перенесённых;
    /// 0 — переносить было нечего, домен не наш.
    ///
    /// Перенесённый `charoite.root` — это ПАПКА ДАННЫХ, а не разрешение
    /// исполнять её код: у старого домена ключа `charoite.codeFromRoot` не
    /// было, а его отсутствие читается как «можно» (договор #328 для явно
    /// выбранного пути). Значит апгрейд молча возвращал исполнение
    /// `src/daemon.py` из записываемого клона с правами приложения — та же
    /// дверь TCC, что закрывали #328/#329 (аудит DeepSeek 16.08). Пишем
    /// `false` явно, зеркально `adoptLegacyCloneAsDataRoot`; разработчик
    /// включит тумблер сам, в Настройках, где это видно.
    @discardableResult
    static func migrateSettings(from old: [String: Any], into d: UserDefaults) -> Int {
        let moved = old.filter { $0.key.hasPrefix("charoit") || $0.key.hasPrefix("sufler.") }
        guard !moved.isEmpty else { return 0 }
        for (k, v) in moved { d.set(v, forKey: k) }
        if moved["charoite.root"] != nil, moved["charoite.codeFromRoot"] == nil {
            d.set(false, forKey: "charoite.codeFromRoot")
        }
        return moved.count
    }

    /// Каталог для байткода вложенного python вне бандла: подписанный .app
    /// должен оставаться ровно таким, каким его нотаризовали. Возвращает путь
    /// (тесты проверяют, что он вне бандла и создан).
    @discardableResult
    static func keepBundleSealed(cachesBase: URL? = nil) -> String {
        let base = cachesBase
            ?? FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory())
        let dir = base.appendingPathComponent("ai.charoite.app/pycache", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        setenv("PYTHONPYCACHEPREFIX", dir.path, 1)
        return dir.path
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Байткод вложенного python — в кэш пользователя, а не в бандл. Без
        // этого первый же запуск демона пишет __pycache__/*.pyc внутрь
        // подписанного .app, печать ресурсов ломается, и у скачавшего DMG
        // второй запуск Gatekeeper встречает «повреждено» (18.08, первый
        // подписанный релиз 0.52.0). Переменную наследуют все дочерние
        // процессы: демон, обработка встреч, ревизии, доктор.
        Self.keepBundleSealed()
        // запись в лопнувший pipe демона (умер между send и daemonDied) без
        // этого валила всё приложение сигналом 13
        signal(SIGPIPE, SIG_IGN)
        // Тап-агрегаты, осиротевшие после прошлого запуска, убираем первым
        // делом: 06.08 такой сирота подвесил CoreAudio целиком.
        if #available(macOS 14.4, *) { SystemAudioTap.cleanupOrphans() }
        Self.migrateSettingsFromOldBundle()
        MeetingNotificationService.shared.configure()
        MeetingProcessingService.shared.startMonitoring()
        _ = DictationService.shared  // регистрирует глобальные ⌥⌘D и ⌥⌘N
        // Папка импорта переживает перезапуск: тумблер в Настройках включён —
        // следим с первого запуска, не дожидаясь открытия настроек
        let d = UserDefaults.standard
        if d.bool(forKey: "charoite.importWatch"),
           let dir = d.string(forKey: "charoite.importDir"), !dir.isEmpty {
            ImportService.shared.enable(dir: dir)
        }
        // Календарный контур принадлежит приложению, а не окну: при запуске
        // через Login Items главное окно может ни разу не открыться, но
        // напоминание о встрече всё равно должно прийти.
        if d.bool(forKey: "charoite.calendarBriefs") {
            CalendarService.shared.enable()
        }
        // Тихий прогрев brain-компаньона: холодный первый скан графа мог не
        // уложиться в таймаут запроса — первый вопрос пользователя падал на
        // медленный локальный фолбэк. Пустой запрос строит кэш заранее.
        Task.detached(priority: .background) {
            _ = await ArchiveSearch.search(query: "прогрев", limit: 1, snippet: 100)
        }
        // Встречи — в системный Spotlight (индекс локальный, машину не покидает).
        SpotlightIndexService.shared.enable()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return false
    }

    /// Клик по иконке в Dock при закрытом окне должен ОТКРЫВАТЬ окно.
    ///
    /// SwiftUI сам так делает, но только пока в сценах нет MenuBarExtra: с ним
    /// приложение «не считается» безоконным, reopen проглатывается — и клик по
    /// Dock молчит. Открываем руками: скрытое окно показываем, уничтоженное —
    /// пересоздаём через пункт меню File → New Window (Cmd+N).
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        guard !flag else { return true }
        Self.showMainWindow()
        if sender.windows.contains(where: { $0.isVisible && $0.canBecomeMain }) { return false }
        return true   // сами не смогли — пусть пробует система
    }

    /// Поднять главное окно из Dock, меню-бара или действия уведомления.
    static func showMainWindow() {
        let app = NSApplication.shared
        app.activate(ignoringOtherApps: true)
        // Открыли существующее → не создаём второе окно поверх.
        if let win = app.windows.first(where: {
            $0.identifier?.rawValue.hasPrefix("main") == true && $0.canBecomeMain
        }) {
            win.makeKeyAndOrderFront(nil)
            return
        }
        if let fileMenu = app.mainMenu?.items.first(where: {
            $0.submenu?.items.contains(where: { $0.keyEquivalent == "n" }) == true
        }),
           let newWindow = fileMenu.submenu?.items.first(where: {
               $0.keyEquivalent == "n" && $0.isEnabled
           }),
           let action = newWindow.action {
            app.sendAction(action, to: newWindow.target, from: newWindow)
        }
    }

    /// Выход посреди встречи — самая дорогая случайность: цена промаха —
    /// оборванная запись, которую уже не переснять.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        let sufler = SuflerService.shared
        guard sufler.hasActiveLifecycle else { return .terminateNow }
        let alert = NSAlert()
        if sufler.lifecycle == .stopping {
            // Capture уже закрыт, поэтому обещать «продолжить встречу» нельзя.
            // Живой daemon всё ещё держит ресурсы и требует явного решения.
            alert.messageText = L.t(
                "Завершение записи ещё не закончено",
                "The recording is still stopping",
                "录音仍在停止中"
            )
            alert.informativeText = L.t(
                "Если выйти сейчас, процесс записи может остаться незавершённым.",
                "Quitting now may leave the recording process unfinished.",
                "现在退出可能会使录音进程未完成。"
            )
            alert.addButton(withTitle: L.t("Остаться и подождать", "Stay and wait", "留下并等待"))
            alert.addButton(withTitle: L.t("Выйти всё равно", "Quit anyway", "仍然退出"))
        } else {
            // Самый дорогой диалог приложения — и он был только на русском:
            // англоязычный пользователь читал две кириллические кнопки наугад,
            // а ценой промаха была оборванная запись встречи.
            alert.messageText = L.t("Идёт запись встречи", "A meeting is being recorded", "正在录制会议")
            alert.informativeText = L.t(
                "Если выйти сейчас, запись прервётся. Стенограмма сохранится, но всё, что скажут дальше, потеряется.",
                "Quitting now stops the recording. The transcript is kept, but everything said after this is lost.",
                "现在退出会中断录音。逐字稿会保留，但此后所说的内容都会丢失。")
            alert.addButton(withTitle: L.t("Продолжить встречу", "Keep recording", "继续录制"))
            alert.addButton(withTitle: L.t("Выйти и остановить запись", "Quit and stop recording", "退出并停止录音"))
        }
        alert.buttons.last?.hasDestructiveAction = true
        if alert.runModal() == .alertFirstButtonReturn { return .terminateCancel }
        // Демону нужно успеть закрыть аудио-стримы и дописать граф встречи.
        // Три секунды тут стояли произвольно и были МЕНЬШЕ грейса, который
        // назначает себе сам stop() (8с на terminate, 12с на SIGKILL): оба
        // добивающих таймера умирали вместе с процессом на третьей секунде,
        // и зависший демон оставался сиротой — держал flock, из-за чего
        // следующий запуск приложения молча отскакивал.
        sufler.stop()
        DispatchQueue.main.asyncAfter(deadline: .now() + 14.0) {
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        if SuflerService.shared.hasActiveLifecycle { SuflerService.shared.stop() }
        // Тап не должен переживать приложение: живой агрегат без хозяина —
        // это подвешенный CoreAudio (дважды за 06.08). stop() выше гасит
        // его только при идущей записи — добираем оставшееся всегда.
        if #available(macOS 14.4, *) { SystemAudioTap.cleanupOrphans() }
    }

    /// charoite:// — управление из Shortcuts, терминала и других приложений:
    ///   charoite://record/start · stop · toggle — запись встречи
    ///   charoite://meeting/<id> — открыть карточку (id как в Spotlight)
    ///   charoite://tasks · today — разделы
    ///
    /// Любое действие поднимает окно: старт записи по ссылке обязан быть
    /// виден. Ссылку может дёрнуть и веб-страница — но браузер спрашивает
    /// подтверждение, а «тихой» записи не существует: окно, статус и таймер
    /// на экране.
    func application(_ application: NSApplication, open urls: [URL]) {
        for url in urls where url.scheme == "charoite" {
            let command = url.host() ?? ""
            let argument = url.path().trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            switch (command, argument) {
            case ("record", "start"):
                Self.showMainWindow()
                SuflerService.shared.start()
            case ("record", "stop"):
                SuflerService.shared.stop()
            case ("record", "toggle"):
                Self.showMainWindow()
                SuflerService.shared.toggle()
            case ("meeting", let id) where !id.isEmpty:
                WorkspaceNavigation.shared.open(.meeting, meetingID: id)
            case ("tasks", _):
                WorkspaceNavigation.shared.openTasks()
            case ("today", _):
                WorkspaceNavigation.shared.open(.today)
            default:
                NSLog("charoite:// неизвестная команда: %@", url.absoluteString)
            }
        }
    }
}

#endif
