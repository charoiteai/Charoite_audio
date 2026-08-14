import AppKit
import Foundation

#if os(macOS)

/// Установка и запуск Ollama без терминала.
///
/// Инструкция начиналась с двух команд в терминале, и это был первый барьер
/// на пути человека, который только что перетащил приложение в «Программы».
/// Хуже того, обе команды легко выполнить неправильно: `brew install ollama`
/// не поднимает сервер, а поставить brew-версию поверх Ollama.app — значит
/// получить два экземпляра, дерущихся за порт 11434. Ровно это и случилось
/// 13.08: приложение занимало порт, brew-сервис висел в `error`, обновления
/// не применялись, а снаружи это выглядело безобидным предупреждением о
/// несовпадении версий клиента и сервера.
///
/// Поэтому приложение само отвечает на три вопроса: установлена ли Ollama,
/// запущена ли она, и если нет — что нажать.
enum OllamaRuntime: Equatable {
    /// Порт отвечает — рантайм готов.
    case running
    /// Бинарь есть, сервер молчит. Знаем, чем именно поднимать.
    case installedNotRunning(launcher: Launcher)
    /// Ollama на машине нет.
    case notInstalled(canUseBrew: Bool)

    /// Чем поднимать сервер: у brew и у приложения это разные команды, и
    /// перепутать их — значит поднять второй экземпляр рядом с первым.
    enum Launcher: Equatable {
        case brewService
        case appBundle(path: String)
    }
}

@MainActor
final class OllamaRuntimeService: ObservableObject {
    static let shared = OllamaRuntimeService()

    @Published private(set) var state: OllamaRuntime = .running
    @Published private(set) var busy: String?
    @Published private(set) var failure: String?

    private init() {}

    // MARK: - Чистая логика (её и держат тесты)

    /// Где искать бинарь. Порядок важен: brew первым, потому что именно он
    /// рекомендован — при обоих установках мы поднимаем brew-сервис, а не
    /// второй экземпляр из приложения.
    static let brewPaths = ["/opt/homebrew/bin/ollama", "/usr/local/bin/ollama"]
    static let appPath = "/Applications/Ollama.app"

    /// Состояние по фактам: отвечает ли порт, какие пути существуют.
    ///
    /// Отдельная функция без обращений к диску и сети — иначе состояние
    /// «что показать человеку» проверялось бы только руками на живой машине.
    nonisolated static func decide(responding: Bool,
                                   brewBinary: String?,
                                   appBundleExists: Bool,
                                   brewAvailable: Bool) -> OllamaRuntime {
        if responding { return .running }
        if brewBinary != nil { return .installedNotRunning(launcher: .brewService) }
        if appBundleExists { return .installedNotRunning(launcher: .appBundle(path: appPath)) }
        return .notInstalled(canUseBrew: brewAvailable)
    }

    /// Что написать на кнопке. Пустая строка — кнопки нет.
    nonisolated static func actionTitle(for state: OllamaRuntime) -> String {
        switch state {
        case .running:
            return ""
        case .installedNotRunning:
            return L.t("Запустить", "Start", "启动")
        case .notInstalled:
            return L.t("Установить", "Install", "安装")
        }
    }

    /// Объяснение состояния — то, что человек читает до нажатия.
    nonisolated static func explanation(for state: OllamaRuntime) -> String {
        switch state {
        case .running:
            return L.t("Локальный движок моделей работает",
                       "The local model runtime is running",
                       "本地模型运行时已就绪")
        case .installedNotRunning:
            return L.t("Движок установлен, но не запущен — поднимем сам",
                       "The runtime is installed but not running — we'll start it",
                       "运行时已安装但未启动——我们来启动它")
        case .notInstalled(let canUseBrew) where canUseBrew:
            return L.t("Нужен локальный движок моделей — поставим через Homebrew",
                       "A local model runtime is required — we'll install it via Homebrew",
                       "需要本地模型运行时——将通过 Homebrew 安装")
        case .notInstalled:
            return L.t("Нужен локальный движок моделей — откроем страницу загрузки",
                       "A local model runtime is required — we'll open the download page",
                       "需要本地模型运行时——将打开下载页面")
        }
    }

    // MARK: - Действия

    func refresh() async {
        let responding = await Self.responds()
        let brew = Self.brewPaths.first { FileManager.default.isExecutableFile(atPath: $0) }
        state = Self.decide(responding: responding,
                            brewBinary: brew,
                            appBundleExists: FileManager.default.fileExists(atPath: Self.appPath),
                            brewAvailable: Self.brewBinary() != nil)
    }

    /// Одно действие на все три состояния: человеку не нужно знать, чем у
    /// него поставлена Ollama и почему она молчит.
    func fix() async {
        guard busy == nil else { return }
        failure = nil
        switch state {
        case .running:
            return
        case .installedNotRunning(.brewService):
            busy = L.t("запускаю…", "starting…", "启动中…")
            await run(Self.brewBinary() ?? "/opt/homebrew/bin/brew", ["services", "start", "ollama"])
        case .installedNotRunning(.appBundle(let path)):
            busy = L.t("запускаю…", "starting…", "启动中…")
            await run("/usr/bin/open", ["-a", path])
        case .notInstalled(let canUseBrew):
            if canUseBrew, let brew = Self.brewBinary() {
                busy = L.t("устанавливаю…", "installing…", "安装中…")
                await run(brew, ["install", "ollama"])
                await run(brew, ["services", "start", "ollama"])
            } else {
                // Без Homebrew ставить молча нечего: официальный установщик
                // требует участия человека, и подсовывать ему скачанный нами
                // образ мимо его же выбора — не наша роль.
                NSWorkspace.shared.open(URL(string: "https://ollama.com/download")!)
            }
        }
        // Сервер поднимается не мгновенно: ждём ответа порта, а не факта
        // запуска процесса — иначе скажем «готово» раньше времени.
        for _ in 0..<20 {
            if await Self.responds() { break }
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
        busy = nil
        await refresh()
    }

    // MARK: - Внутреннее

    private static func brewBinary() -> String? {
        ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"]
            .first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    private static func responds() async -> Bool {
        guard let url = URL(string: AppSettings.ollamaURL + "/api/tags") else { return false }
        let cfg = URLSessionConfiguration.ephemeral
        // Локальный адрес мимо системного прокси: 13.08 прокси в системных
        // настройках отправлял в туннель даже обращения к 127.0.0.1.
        cfg.connectionProxyDictionary = [:]
        cfg.timeoutIntervalForRequest = 3
        guard let (_, response) = try? await URLSession(configuration: cfg).data(from: url) else {
            return false
        }
        return (response as? HTTPURLResponse)?.statusCode == 200
    }

    private func run(_ tool: String, _ args: [String]) async {
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            DispatchQueue.global(qos: .userInitiated).async {
                let p = Process()
                p.executableURL = URL(fileURLWithPath: tool)
                p.arguments = args
                let err = Pipe()
                p.standardOutput = FileHandle.nullDevice
                p.standardError = err
                do {
                    try p.run()
                    p.waitUntilExit()
                    if p.terminationStatus != 0 {
                        let text = String(data: err.fileHandleForReading.readDataToEndOfFile(),
                                          encoding: .utf8) ?? ""
                        Task { @MainActor in
                            self.failure = text.isEmpty
                                ? "\(tool) → \(p.terminationStatus)"
                                : String(text.prefix(200))
                        }
                    }
                } catch {
                    Task { @MainActor in self.failure = error.localizedDescription }
                }
                cont.resume()
            }
        }
    }
}

#endif
