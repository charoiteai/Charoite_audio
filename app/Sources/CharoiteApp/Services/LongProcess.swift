import Darwin
import Foundation

/// Начало строки stderr установщика, когда отмена пришла после фиксации замены:
/// движок уже поставлен, установщик доделывает уборку и выходит кодом 0
/// (`scripts/install_engine.py:431`, `_cancel`).
let installCleanupAfterCommitPrefix = "отмена пришла после замены"

/// Исход долгого процесса — публикуется один раз, когда процесс вышел и оба
/// канала дали EOF (или истёк потолок ожидания EOF).
struct LongProcessOutcome: Equatable {
    var code: Int32
    var reason: Process.TerminationReason
    /// Отмену попросили, пока лидер группы был жив (или до запуска).
    var cancelRequested: Bool
    /// Процесс не запускался: отмена пришла раньше `run()` или `run()` бросил.
    var notStarted: Bool
    var stdout: String
    /// Последняя непустая строка stderr.
    var stderrTail: String
    /// В stderr была строка «отмена пришла после замены».
    var cleanupAfterCommit: Bool
}

/// Долгий python-процесс Nemotron (установка, `--check`, `--plan`) с отменой
/// по группе процессов.
///
/// Почему не копия «SIGTERM → SIGKILL» из `MeetingProcessingService`: копии
/// бьют по pid лидера, а установщик держит детей — pip и пробу; сирота pip
/// дописывал бы 600 МБ после отмены. `Process` запускает ребёнка лидером своей
/// группы (тест закрепляет), поэтому `kill(-pid, …)` достаёт всю группу.
///
/// Два канала читаются по мере поступления: строки stdout — ход для кнопки,
/// stderr копится ради последней строки. Исход ждёт и выхода, и EOF обоих
/// каналов: последняя строка JSON у `--plan` и строка «отмена пришла после
/// замены» приходят после последнего колбэка. Внук с унаследованным каналом
/// держит EOF сколько угодно, поэтому ожидание EOF ограничено `eofCeiling`,
/// а по потолку группа добивается SIGKILL.
final class LongProcess: @unchecked Sendable {
    /// Посылка сигнала: 0 — доставлен, иначе errno (`ESRCH` — адресата нет).
    typealias SignalFunction = @Sendable (pid_t, Int32) -> Int32

    static let defaultSignal: SignalFunction = { target, sig in
        Darwin.kill(target, sig) == 0 ? 0 : errno
    }

    private let process = Process()
    private let eofCeiling: TimeInterval
    private let signalFunction: SignalFunction
    private let onLine: (@MainActor (String) -> Void)?
    private let onCleanupAfterCommit: (@MainActor () -> Void)?
    private let outPipe = Pipe()
    private let errPipe = Pipe()

    private let lock = NSLock()
    private var pid: pid_t = 0
    private var exited = false
    private var cancelRequested = false
    private var published = false
    private var termSent = false
    private var killDeadline: Date?
    private var killTimer: DispatchWorkItem?
    private var stdoutEOF = false
    private var closing = false
    /// Вызовы обработчика канала после его EOF — для теста (обработчик снят).
    private var callsAfterEOF = 0
    var handlerCallsAfterEOF: Int {
        lock.lock(); defer { lock.unlock() }
        return callsAfterEOF
    }
    private var stderrEOF = false
    private var continuation: CheckedContinuation<LongProcessOutcome, Never>?

    private let stdoutBuffer = LineBuffer(keep: 8 << 20)
    private let stderrBuffer = LineBuffer(keep: 0)
    private var stderrLast = ""
    private var cleanupAfterCommit = false

    /// `arguments` — путь скрипта относительно корня кода и его ключи.
    /// `root` даёт служба: помощник сам папку данных не читает.
    init(arguments: [String],
         root: URL,
         executable: URL? = nil,
         eofCeiling: TimeInterval = 2,
         signal: @escaping SignalFunction = LongProcess.defaultSignal,
         onLine: (@MainActor (String) -> Void)? = nil,
         onCleanupAfterCommit: (@MainActor () -> Void)? = nil) {
        self.eofCeiling = eofCeiling
        self.signalFunction = signal
        self.onLine = onLine
        self.onCleanupAfterCommit = onCleanupAfterCommit
        process.arguments = arguments
        // Установщик и `--check` становятся лидером своей группы, если ещё не
        // лидеры; `--plan` группу не трогает — переменная ему не вредит.
        AppSettings.preparePython(process, root: root, executable: executable,
                                  extraEnvironment: ["CHAROITE_INSTALL_NEW_PGROUP": "1"])
        process.standardOutput = outPipe
        process.standardError = errPipe
    }

    /// pid запущенного процесса; 0 — ещё не запущен (для тестов и журнала).
    var processID: pid_t {
        lock.lock(); defer { lock.unlock() }
        return pid
    }

    /// Запустить и дождаться исхода. Главный поток не блокируется.
    func run() async -> LongProcessOutcome {
        await withCheckedContinuation { (cont: CheckedContinuation<LongProcessOutcome, Never>) in
            start(cont)
        }
    }

    private func start(_ cont: CheckedContinuation<LongProcessOutcome, Never>) {
        outPipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            self?.received(h.availableData, stdout: true, handle: h)
        }
        errPipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            self?.received(h.availableData, stdout: false, handle: h)
        }
        process.terminationHandler = { [weak self] p in
            self?.leaderExited(status: p.terminationStatus, reason: p.terminationReason)
        }

        lock.lock()
        continuation = cont
        // Проверка отмены и запуск — одна критическая секция: `cancel()` до
        // запуска не встретит pid 0 (`kill(-0, …)` бьёт по группе приложения).
        if cancelRequested {
            lock.unlock()
            dropHandlers()
            publish(code: 0, reason: .exit, notStarted: true, error: nil)
            return
        }
        do {
            try process.run()
            pid = process.processIdentifier
            lock.unlock()
        } catch {
            lock.unlock()
            dropHandlers()
            publish(code: 127, reason: .exit, notStarted: true, error: error.localizedDescription)
        }
    }

    /// Отмена: SIGTERM всей группе, через `grace` — SIGKILL.
    ///
    /// После выхода лидера код 0 — уже «поставлено»: отмена тогда только
    /// добивает группу и исход не меняет. Повторная отмена сигналов заново не
    /// шлёт, но может приблизить SIGKILL (выход из приложения после «Отмены»).
    func cancel(grace: TimeInterval) {
        lock.lock()
        if published { lock.unlock(); return }
        if pid == 0 {
            cancelRequested = true
            lock.unlock()
            return
        }
        if !exited { cancelRequested = true }
        let sendTerm = !termSent
        termSent = true
        let deadline = Date().addingTimeInterval(grace)
        var schedule = false
        if killDeadline == nil || deadline < killDeadline! {
            killDeadline = deadline
            killTimer?.cancel()
            schedule = true
        }
        let target = pid
        let leaderExited = exited
        var item: DispatchWorkItem?
        if schedule {
            let work = DispatchWorkItem { [weak self] in self?.killGroup() }
            killTimer = work
            item = work
        }
        lock.unlock()

        if sendTerm { signalGroup(target, SIGTERM, leaderExited: leaderExited) }
        if let item {
            DispatchQueue.global().asyncAfter(deadline: .now() + grace, execute: item)
        }
    }

    private func killGroup() {
        lock.lock()
        if published { lock.unlock(); return }
        let target = pid
        let leaderExited = exited
        lock.unlock()
        signalGroup(target, SIGKILL, leaderExited: leaderExited)
    }

    /// Группе — всегда; запасной сигнал по pid — только пока лидер жив: после
    /// выхода pid освобождён и мог достаться чужому процессу.
    private func signalGroup(_ target: pid_t, _ sig: Int32, leaderExited: Bool) {
        let result = send(-target, sig)
        if result == ESRCH && !leaderExited {
            _ = send(target, sig)
        }
    }

    /// Обёртка вокруг функции сигнала: pid 0, 1 и −1 (и их отрицания) —
    /// отказ. `kill(0, …)` бьёт по группе самого приложения, `kill(-1, …)` —
    /// по всем процессам пользователя.
    func send(_ target: pid_t, _ sig: Int32) -> Int32 {
        guard target > 1 || target < -1 else { return EINVAL }
        return signalFunction(target, sig)
    }

    // MARK: - Каналы

    private func received(_ data: Data, stdout: Bool, handle: FileHandle) {
        lock.lock()
        let afterEOF = stdout ? stdoutEOF : stderrEOF
        if afterEOF { callsAfterEOF += 1 }
        lock.unlock()
        if afterEOF { return }
        if data.isEmpty {
            // EOF: снять обработчик сразу, иначе он крутится вхолостую,
            // пока второй канал ещё открыт.
            handle.readabilityHandler = nil
            // Хвост без перевода строки — до отметки EOF: отметка открывает
            // публикацию, и хвост опоздал бы к исходу.
            flush(stdout: stdout)
            lock.lock()
            if stdout { stdoutEOF = true } else { stderrEOF = true }
            lock.unlock()
            maybePublish()
            return
        }
        consume(data, stdout: stdout)
    }

    private func consume(_ data: Data, stdout: Bool) {
        let lines = (stdout ? stdoutBuffer : stderrBuffer).append(data)
        handle(lines, stdout: stdout)
    }

    private func flush(stdout: Bool) {
        if let rest = (stdout ? stdoutBuffer : stderrBuffer).flush() {
            handle([rest], stdout: stdout)
        }
    }

    private func handle(_ lines: [String], stdout: Bool) {
        guard !lines.isEmpty else { return }
        if stdout {
            guard let onLine else { return }
            // Очередь главного потока сохраняет порядок строк.
            for line in lines {
                DispatchQueue.main.async { MainActor.assumeIsolated { onLine(line) } }
            }
            return
        }
        lock.lock()
        let before = cleanupAfterCommit
        for line in lines {
            let t = line.trimmingCharacters(in: .whitespacesAndNewlines)
            if t.isEmpty { continue }
            stderrLast = t
            if t.hasPrefix(installCleanupAfterCommitPrefix) { cleanupAfterCommit = true }
        }
        let firstSeen = cleanupAfterCommit && !before
        lock.unlock()
        if firstSeen, let onCleanupAfterCommit {
            // Отмена пришла после замены: кнопка говорит «доделываю уборку».
            DispatchQueue.main.async { MainActor.assumeIsolated { onCleanupAfterCommit() } }
        }
    }

    private func dropHandlers() {
        outPipe.fileHandleForReading.readabilityHandler = nil
        errPipe.fileHandleForReading.readabilityHandler = nil
    }

    // MARK: - Выход и исход

    private var exitStatus: Int32 = 0
    private var exitReason: Process.TerminationReason = .exit

    private func leaderExited(status: Int32, reason: Process.TerminationReason) {
        lock.lock()
        exited = true
        exitStatus = status
        exitReason = reason
        lock.unlock()
        if !maybePublish() {
            DispatchQueue.global().asyncAfter(deadline: .now() + eofCeiling) { [weak self] in
                self?.eofCeilingReached()
            }
        }
    }

    /// Публикует, если лидер вышел и оба канала дали EOF.
    @discardableResult
    private func maybePublish() -> Bool {
        lock.lock()
        let ready = exited && stdoutEOF && stderrEOF && !published && !closing
        let status = exitStatus, reason = exitReason
        lock.unlock()
        guard ready else { return false }
        publish(code: status, reason: reason, notStarted: false, error: nil)
        return true
    }

    /// Потолок EOF: дочитать доступное без блокировки, закрыть каналы и сразу
    /// добить группу — лидер вышел, а держатель канала иначе остался бы сиротой.
    private func eofCeilingReached() {
        lock.lock()
        if published { lock.unlock(); return }
        let target = pid
        let status = exitStatus, reason = exitReason
        let needOut = !stdoutEOF, needErr = !stderrEOF
        // Пока дочитываем, обычная публикация по EOF не должна опередить.
        closing = true
        lock.unlock()
        dropHandlers()
        if needOut {
            consume(Self.drainNonBlocking(outPipe.fileHandleForReading), stdout: true)
            flush(stdout: true)
        }
        if needErr {
            consume(Self.drainNonBlocking(errPipe.fileHandleForReading), stdout: false)
            flush(stdout: false)
        }
        try? outPipe.fileHandleForReading.close()
        try? errPipe.fileHandleForReading.close()
        _ = send(-target, SIGKILL)
        publish(code: status, reason: reason, notStarted: false, error: nil)
    }

    private static func drainNonBlocking(_ handle: FileHandle) -> Data {
        let fd = handle.fileDescriptor
        let flags = fcntl(fd, F_GETFL)
        _ = fcntl(fd, F_SETFL, flags | O_NONBLOCK)
        var out = Data()
        var buf = [UInt8](repeating: 0, count: 65_536)
        while true {
            let n = buf.withUnsafeMutableBytes { Darwin.read(fd, $0.baseAddress, $0.count) }
            if n <= 0 { break }
            out.append(buf, count: n)
        }
        return out
    }

    private func publish(code: Int32, reason: Process.TerminationReason,
                         notStarted: Bool, error: String?) {
        lock.lock()
        if published { lock.unlock(); return }
        published = true
        killTimer?.cancel()
        killTimer = nil
        let outcome = LongProcessOutcome(
            code: code,
            reason: reason,
            cancelRequested: cancelRequested,
            notStarted: notStarted,
            stdout: stdoutBuffer.text,
            stderrTail: error ?? stderrLast,
            cleanupAfterCommit: cleanupAfterCommit)
        let cont = continuation
        continuation = nil
        lock.unlock()
        cont?.resume(returning: outcome)
    }
}

/// Накопитель канала: режет поток на строки, держит недописанный хвост и
/// (при `keep > 0`) весь текст канала, но не больше `keep` байт с конца.
private final class LineBuffer: @unchecked Sendable {
    private let lock = NSLock()
    private let keep: Int
    private var all = Data()
    private var partial = Data()

    init(keep: Int) { self.keep = keep }

    func append(_ chunk: Data) -> [String] {
        lock.lock(); defer { lock.unlock() }
        if keep > 0 {
            all.append(chunk)
            if all.count > keep { all = all.suffix(keep) }
        }
        partial.append(chunk)
        var lines: [String] = []
        while let nl = partial.firstIndex(of: 0x0A) {
            lines.append(String(decoding: partial[partial.startIndex..<nl], as: UTF8.self))
            partial = partial[partial.index(after: nl)...]
        }
        partial = Data(partial)
        return lines
    }

    func flush() -> String? {
        lock.lock(); defer { lock.unlock() }
        guard !partial.isEmpty else { return nil }
        let rest = String(decoding: partial, as: UTF8.self)
        partial = Data()
        return rest
    }

    var text: String {
        lock.lock(); defer { lock.unlock() }
        return String(decoding: all, as: UTF8.self)
    }
}
