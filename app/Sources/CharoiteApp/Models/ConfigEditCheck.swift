import Darwin
import Foundation

/// Проверка правки `config.yaml` кнопкой Nemotron тем же парсером, что у демона
/// (`yaml.safe_load`): `scripts/install_engine.py --verify-config-edit` (№622, круг fix6).
///
/// Построчный разбор приложения — первый фильтр, не оракул: семь кругов подряд
/// находили контекст документа, которого строка не видит, — ключ внутри чужой
/// кавычки или flow-скобки выше, второй блок `sufler:`, секция во flow-форме.
/// Интерпретатор и код — те же, которыми кнопка зовёт установщик
/// (`AppSettings.preparePython`, `scriptPath`). Процесс не запустился, упал,
/// не уложился в срок или ответил не `ok` — отказ записи.
struct ConfigEditCheck: Sendable {
    /// `nil` — интерпретатор приложения для корня данных; тесты подставляют свой.
    var python: URL?
    /// `nil` — `scripts/install_engine.py` из корня кода.
    var script: String?
    var timeout: TimeInterval = 10

    static let live = ConfigEditCheck()

    /// Проверка, которой пользуется условная запись; тесты подменяют интерпретатор.
    @MainActor static var current = ConfigEditCheck.live

    /// `nil` — правка верна; иначе причина отказа человеку.
    func refusal(old: String, new: String, key: String, expected: String?, value: String,
                 root: URL) async -> String? {
        let body: [String: Any] = ["old": old, "new": new, "key": key,
                                   "expected": expected ?? NSNull(), "value": value]
        guard let payload = try? JSONSerialization.data(withJSONObject: body) else {
            return Self.prefix + "не собран вход"
        }
        let process = Process()
        AppSettings.preparePython(process, root: root, executable: python)
        process.arguments = [script ?? AppSettings.scriptPath("scripts/install_engine.py", root: root),
                             "--verify-config-edit"]
        let timeout = timeout
        return await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                cont.resume(returning: Self.run(process, payload: payload, timeout: timeout))
            }
        }
    }

    private static let prefix = L.t("проверка записи config.yaml: ", "config.yaml write check: ",
                                    "config.yaml 写入检查：")

    /// Блокирующий прогон — вне главного потока.
    private static func run(_ process: Process, payload: Data, timeout: TimeInterval) -> String? {
        let input = Pipe(), output = Pipe(), errors = Pipe()
        process.standardInput = input
        process.standardOutput = output
        process.standardError = errors
        // Ребёнок, вышедший не читая stdin, не должен валить приложение SIGPIPE.
        _ = fcntl(input.fileHandleForWriting.fileDescriptor, F_SETNOSIGPIPE, 1)
        let exited = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in exited.signal() }
        do {
            try process.run()
        } catch {
            return prefix + L.t("не запустилась — ", "did not start — ", "未能启动——") + error.localizedDescription
        }
        let pid = process.processIdentifier
        let readers = DispatchGroup()
        let out = Box(), err = Box()
        for (pipe, box) in [(output, out), (errors, err)] {
            readers.enter()
            DispatchQueue.global(qos: .userInitiated).async {
                box.set(pipe.fileHandleForReading.readDataToEndOfFile())
                readers.leave()
            }
        }
        DispatchQueue.global(qos: .userInitiated).async {
            try? input.fileHandleForWriting.write(contentsOf: payload)
            try? input.fileHandleForWriting.close()
        }
        if exited.wait(timeout: .now() + timeout) == .timedOut {
            kill(pid, SIGKILL)
            _ = exited.wait(timeout: .now() + 2)
            return prefix + L.t("не уложилась в \(Int(timeout)) с", "timed out after \(Int(timeout)) s",
                                "超时（\(Int(timeout)) 秒）")
        }
        _ = readers.wait(timeout: .now() + 2)
        let line = (String(bytes: out.get(), encoding: .utf8) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if process.terminationReason == .exit, process.terminationStatus == 0, line == "ok" { return nil }
        if line.hasPrefix("отказ: ") { return prefix + line.dropFirst("отказ: ".count) }
        let tail = (String(bytes: err.get(), encoding: .utf8) ?? "")
            .split(whereSeparator: \.isNewline).last.map(String.init) ?? ""
        return prefix + L.t("процесс вышел с кодом ", "process exited with code ", "进程退出码 ")
            + "\(process.terminationStatus)" + (tail.isEmpty ? "" : " — \(tail)")
    }

    private final class Box: @unchecked Sendable {
        private let lock = NSLock()
        private var data = Data()
        func set(_ d: Data) { lock.lock(); data = d; lock.unlock() }
        func get() -> Data { lock.lock(); defer { lock.unlock() }; return data }
    }
}
