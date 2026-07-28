import SwiftUI

#if os(macOS)

@main
struct CharoiteApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        WindowGroup(L.t("Чароит — суфлёр", "Charoite — meeting copilot", "Charoite — 会议提词"), id: "main") {
            SuflerView()
        }
        .defaultSize(width: 1100, height: 700)

        Window(L.t("Чат с памятью", "Chat with memory", "记忆聊天"), id: "localchat") {
            LocalChatView()
                .frame(minWidth: 520, minHeight: 420)   // минимум только у окна: в панели вью уже
        }
        .defaultSize(width: 640, height: 520)

        Window(L.t("Задачи со встреч", "Meeting tasks", "会议任务"), id: "tasks") {
            TasksView()
        }
        .defaultSize(width: 520, height: 480)

        MenuBarExtra {
            MenuBarView()
        } label: {
            Image(systemName: "brain.head.profile")
        }
        .menuBarExtraStyle(.window)

        Settings {
            SettingsView()
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
            let moved = src.dictionaryRepresentation().filter {
                $0.key.hasPrefix("charoit") || $0.key.hasPrefix("sufler.")
            }
            guard !moved.isEmpty else { continue }
            for (k, v) in moved { d.set(v, forKey: k) }
            NSLog("Charoite: перенесено настроек из %@: %d", old, moved.count)
            return
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        // запись в лопнувший pipe демона (умер между send и daemonDied) без
        // этого валила всё приложение сигналом 13
        signal(SIGPIPE, SIG_IGN)
        Self.migrateSettingsFromOldBundle()
        _ = DictationService.shared  // регистрирует глобальные ⌥⌘D и ⌥⌘N
        // Папка импорта переживает перезапуск: тумблер в Настройках включён —
        // следим с первого запуска, не дожидаясь открытия настроек
        let d = UserDefaults.standard
        if d.bool(forKey: "charoite.importWatch"),
           let dir = d.string(forKey: "charoite.importDir"), !dir.isEmpty {
            ImportService.shared.enable(dir: dir)
        }
        // Тихий прогрев brain-компаньона: холодный первый скан графа мог не
        // уложиться в таймаут запроса — первый вопрос пользователя падал на
        // медленный локальный фолбэк. Пустой запрос строит кэш заранее.
        Task.detached(priority: .background) {
            _ = await ArchiveSearch.search(query: "прогрев", limit: 1, snippet: 100)
        }
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
        sender.activate(ignoringOtherApps: true)
        // Открыли сами → false, иначе система откроет ВТОРОЕ окно поверх
        if let win = sender.windows.first(where: {
            $0.identifier?.rawValue.hasPrefix("main") == true && $0.canBecomeMain
        }) {
            win.makeKeyAndOrderFront(nil)
            return false
        }
        if let fileMenu = sender.mainMenu?.items.first(where: { $0.submenu?.items.contains(where: { $0.keyEquivalent == "n" }) == true }),
           let newWindow = fileMenu.submenu?.items.first(where: { $0.keyEquivalent == "n" && $0.isEnabled }),
           let action = newWindow.action {
            sender.sendAction(action, to: newWindow.target, from: newWindow)
            return false
        }
        return true   // сами не смогли — пусть пробует система
    }

    /// Выход посреди встречи — самая дорогая случайность: цена промаха —
    /// оборванная запись, которую уже не переснять.
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard SuflerService.shared.isRunning else { return .terminateNow }
        let alert = NSAlert()
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
        alert.buttons.last?.hasDestructiveAction = true
        if alert.runModal() == .alertFirstButtonReturn { return .terminateCancel }
        // Демону нужно успеть закрыть аудио-стримы и дописать граф встречи.
        // Три секунды тут стояли произвольно и были МЕНЬШЕ грейса, который
        // назначает себе сам stop() (8с на terminate, 12с на SIGKILL): оба
        // добивающих таймера умирали вместе с процессом на третьей секунде,
        // и зависший демон оставался сиротой — держал flock, из-за чего
        // следующий запуск приложения молча отскакивал.
        SuflerService.shared.stop()
        DispatchQueue.main.asyncAfter(deadline: .now() + 14.0) {
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        if SuflerService.shared.isRunning { SuflerService.shared.stop() }
    }
}

#endif
