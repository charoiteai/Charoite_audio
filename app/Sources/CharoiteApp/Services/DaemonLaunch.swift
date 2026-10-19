import Darwin
import Foundation

/// Один запуск демона записи: процесс и все его концы труб.
///
/// Причина отказа демона (`reason` в статусе) приходит строкой stdout перед
/// выходом, а решение «повторять ли запуск» принимается по смерти процесса.
/// Читатель трубы и уведомление о выходе приходят из разных очередей, и
/// пока каждый прыгал на главный актор своим `Task`, порядок между ними не
/// был задан: одной уступки планировщика в потоке читателя хватало, чтобы
/// решение ушло раньше строки, и рецепт отказа превращался в три
/// «восстанавливаю» (№619, опыт 19.10: 5 из 200 при `usleep(0)`, 200 из 200
/// при задержке читателя 1 мс).
///
/// Поэтому смерть запуска — это «процесс вышел И stdout дал EOF» (правило
/// `LongProcess`, но без его дочитки на потолке — см. ниже; один барьер на
/// оба — долг №640). Данные, EOF и выход идут в главный поток одной очередью
/// `DispatchQueue.main` (FIFO): к блоку EOF все блоки данных этой трубы уже
/// выполнены. Внук с унаследованным stdout держит EOF сколько угодно, поэтому
/// после выхода ждём не дольше `eofCeiling` (цена: смерть видна на 2–8 с
/// позже выхода). Потолок продлевается (до `ceilingRearms` раз), пока байт
/// где-то в пути: непрочитан в ядре (`FIONREAD`) или у
/// читателя между чтением и постановкой блока (`reader.inFlight`, под тем же
/// замком). Трубу потолок не читает: дочитка из чужого потока обогнала бы уже
/// запущенного читателя. Решение потолка исполняется отдельным блоком
/// очереди — после блоков данных, поставленных до проверки.
///
/// Ресурсы принадлежат запуску, а не сервису: опоздавший исход старого
/// запуска закрывает свои концы и не трогает трубы преемника. После
/// `close()` запуск молчит: ни строк, ни смерти. Остановка запуск не
/// закрывает: хвост stdout после её финиша разбирается, как до №619, а концы
/// закрывает смерть самого запуска.
@MainActor
final class DaemonLaunch {
    static let eofCeiling: TimeInterval = 2
    static let ceilingRearms = 3

    enum Event { case output(Data), eof, exit }

    let process: Process
    let stdinPipe: Pipe
    let stdout: FileHandle
    let errHandle: FileHandle?
    /// Потолок ожидания EOF после выхода; тесты сжимают его.
    private(set) var eofCeiling = DaemonLaunch.eofCeiling
    #if DEBUG
    func setCeilingForTest(_ s: TimeInterval) { eofCeiling = s }
    /// Задержка читателя перед `availableData`: гонка №619 воспроизводится
    /// ею всегда (1 мс — 200 из 200 на прежней проводке).
    var readerDelayForTest: useconds_t = 0
    #endif

    private(set) var exited = false
    private(set) var reachedEOF = false
    private(set) var closed = false
    private(set) var rearms = 0
    private let reader = ReaderState()
    private var onOutput: (@MainActor (Data) -> Void)?
    private var onDead: (@MainActor (DaemonLaunch) -> Void)?

    /// Читатель отмечает себя до `availableData` и снимает отметку после
    /// постановки блока: потолок под тем же замком видит байт, который уже
    /// вынут из ядра, но ещё не доставлен.
    private final class ReaderState: @unchecked Sendable {
        private let lock = NSLock()
        private var inFlight = false
        func set(_ v: Bool) { lock.lock(); inFlight = v; lock.unlock() }
        func busy(orReadable fd: Int32) -> Bool {
            lock.lock(); defer { lock.unlock() }
            return inFlight || DaemonLaunch.unreadBytes(fd) > 0
        }
    }

    init(process: Process, stdinPipe: Pipe, stdout: FileHandle, errHandle: FileHandle?) {
        self.process = process
        self.stdinPipe = stdinPipe
        self.stdout = stdout
        self.errHandle = errHandle
    }

    /// Трубы stdin/stdout и stderr в `logs/daemon.err.log` для процесса,
    /// который ещё не запущен.
    static func prepare(_ p: Process, logDir: URL) -> DaemonLaunch {
        let outPipe = Pipe()
        let inPipe = Pipe()
        p.standardOutput = outPipe
        p.standardInput = inPipe
        // stderr — в лог: без него сбои демона невидимы (logs/daemon.err.log)
        FileManager.default.createPrivateDirectory(at: logDir)  // логи демона несут куски стенограмм
        let errURL = logDir.appendingPathComponent("daemon.err.log")
        // append, не пересоздание: авто-рестарт после крэша затирал трейсбек
        // ровно в момент, когда он нужен для диагноза
        if !FileManager.default.fileExists(atPath: errURL.path) {
            FileManager.default.createPrivateFile(atPath: errURL.path)   // 0600 сразу, не по umask
        }
        FileManager.default.makePrivate(atPath: errURL.path)   // лог старой установки
        LogTrim.trim(errURL)   // потолок: хвост остаётся, гигабайты — нет
        let errFH = try? FileHandle(forWritingTo: errURL)
        errFH?.seekToEndOfFile()
        p.standardError = errFH ?? FileHandle.nullDevice
        return DaemonLaunch(process: p, stdinPipe: inPipe,
                            stdout: outPipe.fileHandleForReading, errHandle: errFH)
    }

    /// Куда отдавать байты stdout и смерть запуска.
    func attach(onOutput: @escaping @MainActor (Data) -> Void,
                onDead: @escaping @MainActor (DaemonLaunch) -> Void) {
        self.onOutput = onOutput
        self.onDead = onDead
    }

    /// Поставить читателя и обработчик выхода — до `run()`. Каждый колбэк
    /// переходит в главный поток через `DispatchQueue.main.async`, не через
    /// `Task`: порядок между `Task` и блоками очереди не задан.
    func wireHandles() {
        let reader = reader
        #if DEBUG
        let delay = readerDelayForTest
        #endif
        stdout.readabilityHandler = { [weak self] handle in
            reader.set(true)
            defer { reader.set(false) }
            #if DEBUG
            if delay > 0 { usleep(delay) }
            #endif
            let data = handle.availableData
            // EOF: без снятия хендлера dispatch-source жил после смерти демона —
            // CPU-спин пустыми срабатываниями + утечка pipe на каждый рестарт
            if data.isEmpty { handle.readabilityHandler = nil }
            DispatchQueue.main.async {
                MainActor.assumeIsolated { self?.received(data.isEmpty ? .eof : .output(data)) }
            }
        }
        process.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                MainActor.assumeIsolated { self?.received(.exit) }
            }
        }
    }

    #if DEBUG
    /// Тестовый вход: порядок событий, который в проде даёт гонка.
    func receiveForTest(_ event: Event) { received(event) }
    #endif

    /// Единственный вход событий запуска. Закрыт модулю: из него достижима
    /// смерть живого демона и `stop()` по строке autostop (out1 №619).
    private func received(_ event: Event) {
        guard !closed else { return }
        switch event {
        case .output(let data):
            onOutput?(data)
        case .eof:
            reachedEOF = true
            if exited { die() }
        case .exit:
            exited = true
            if reachedEOF { die() } else { armCeiling() }
        }
    }

    /// Процесс вышел, а EOF нет: stdout держит внук или читатель ещё не
    /// забрал байты. Закрытие — отдельным блоком: блок данных, поставленный
    /// читателем до проверки, стоит в очереди раньше и успеет выполниться.
    private func armCeiling() {
        DispatchQueue.main.asyncAfter(deadline: .now() + eofCeiling) { [weak self] in
            MainActor.assumeIsolated {
                guard let self, !self.closed else { return }
                if self.shouldRearmCeiling() { self.armCeiling(); return }
                DispatchQueue.main.async { [weak self] in
                    MainActor.assumeIsolated {
                        guard let self, !self.closed else { return }
                        self.die()
                    }
                }
            }
        }
    }

    /// Продлить ли потолок: да — пока байт в пути (в трубе или у читателя)
    /// и продления не исчерпаны.
    private func shouldRearmCeiling() -> Bool {
        guard rearms < Self.ceilingRearms, reader.busy(orReadable: stdout.fileDescriptor) else { return false }
        rearms += 1
        return true
    }

    /// Последняя строка без перевода строки — тоже строка: в ней может быть
    /// причина отказа. Дописываем её при любой смерти — по EOF и по потолку.
    private func die() {
        onOutput?(Data([0x0A]))
        let dead = onDead
        close()
        dead?(self)
    }

    /// Закрыть концы этого запуска. Повторный вызов ничего не делает.
    func close() {
        guard !closed else { return }
        closed = true
        onOutput = nil
        onDead = nil
        stdout.readabilityHandler = nil
        process.terminationHandler = nil
        try? errHandle?.close()
        // write-end stdin жил до конца сеанса — та же fd-утечка, что у errHandle (аудит 13.09, DS M2)
        try? stdinPipe.fileHandleForWriting.close()
    }

    /// Сколько байт лежит в трубе непрочитанными. Закрытый пустой канал — 0:
    /// ждать там нечего, EOF доставит читатель или решит потолок. `poll` этого
    /// не различает: на macOS закрытая пустая труба тоже даёт `POLLIN` (опыт
    /// 19.10: revents 17 и с байтами, и без).
    nonisolated static func unreadBytes(_ fd: Int32) -> Int32 {
        var n: Int32 = 0
        return ioctl(fd, fionread, &n) == 0 ? n : 0
    }

    /// `FIONREAD` = `_IOR('f', 127, int)`: макрос-функция в Swift не импортируется.
    private nonisolated static let fionread = UInt(IOC_OUT) | UInt((MemoryLayout<Int32>.size & Int(IOCPARM_MASK)) << 16)
        | (UInt(UInt8(ascii: "f")) << 8) | 127
}
