import SwiftUI

#if os(macOS)

@main
struct CharoiteApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        WindowGroup("Чароит — суфлёр", id: "main") {
            SuflerView()
        }
        .defaultSize(width: 1100, height: 700)

        Window("Чат с памятью", id: "localchat") {
            LocalChatView()
                .frame(minWidth: 520, minHeight: 420)   // минимум только у окна: в панели вью уже
        }
        .defaultSize(width: 640, height: 520)

        Window("Задачи со встреч", id: "tasks") {
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
    func applicationDidFinishLaunching(_ notification: Notification) {
        // запись в лопнувший pipe демона (умер между send и daemonDied) без
        // этого валила всё приложение сигналом 13
        signal(SIGPIPE, SIG_IGN)
        _ = DictationService.shared  // регистрирует глобальные ⌥⌘D и ⌥⌘N
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
        alert.messageText = "Идёт запись встречи"
        alert.informativeText = "Если выйти сейчас, запись прервётся. "
            + "Стенограмма сохранится, но всё, что скажут дальше, потеряется."
        alert.addButton(withTitle: "Продолжить встречу")
        alert.addButton(withTitle: "Выйти и остановить запись")
        alert.buttons.last?.hasDestructiveAction = true
        if alert.runModal() == .alertFirstButtonReturn { return .terminateCancel }
        // Демону нужно успеть закрыть аудио-стримы и дописать граф встречи.
        SuflerService.shared.stop()
        DispatchQueue.main.asyncAfter(deadline: .now() + 3.0) {
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        if SuflerService.shared.isRunning { SuflerService.shared.stop() }
    }
}

#endif
