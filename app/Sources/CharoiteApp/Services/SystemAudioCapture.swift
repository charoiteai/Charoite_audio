import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit
import os

#if os(macOS)

/// Захват звука встречи средствами системы: ScreenCaptureKit.
///
/// Третий подход к системному звуку — и первый, который можно включать.
///
/// 1. **BlackHole** (работает, но дорого для человека): сторонний драйвер,
///    пароль администратора, Audio MIDI Setup, Многовыходное устройство.
///    Худший шаг установки; на нём она и обрывалась.
/// 2. **Core Audio process tap** (06–07.08): звук получили, но цикл создания
///    и уничтожения тап-агрегата клинил CoreAudio на macOS 26.5 — динамики
///    машины умолкали после встречи, лечилось только SIGKILL демону звука.
///    Выключен (см. SystemAudioTap).
/// 3. **ScreenCaptureKit** (этот файл): агрегатных устройств не создаёт
///    вообще, поэтому клинить нечему. Проверено пробником 07.08: 12.5 с
///    системного звука и микрофона одним потоком, после остановки список
///    устройств не изменился, звук машины жив.
///
/// Бонус, ради которого стоило: с macOS 15 микрофон приходит **тем же
/// потоком** отдельным типом. Значит демону не нужен PortAudio — вместе с
/// ним уходит целый класс аварий «мёртвый стрим виснет на close».
///
/// Демон читает готовый PCM из файлов: право на запись системного звука
/// система выдаёт процессу-читателю, а дочерний python его не наследует
/// (вердикт разбора 06–07.08).
@available(macOS 13.0, *)
@MainActor
final class SystemAudioCapture: NSObject {

    /// Манифест общий только как указатель на текущую сессию. Сами потоки
    /// обязаны быть уникальны: прошлый capture закрывается после демона и ещё
    /// несколько секунд может писать, пока следующая встреча уже запускается.
    private static var dir: URL {
        AppSettings.charoiteRoot.appendingPathComponent("data", isDirectory: true)
    }
    static var manifestURL: URL { dir.appendingPathComponent("sck_stream.json") }

    struct SessionPaths: Equatable {
        let directory: URL
        let systemURL: URL
        let micURL: URL
    }

    static func sessionPaths(sessionID: UUID) -> SessionPaths {
        let directory = dir
            .appendingPathComponent("sck", isDirectory: true)
            .appendingPathComponent(sessionID.uuidString, isDirectory: true)
        return SessionPaths(
            directory: directory,
            systemURL: directory.appendingPathComponent("system.raw"),
            micURL: directory.appendingPathComponent("mic.raw"))
    }

    /// Частота потоков — сразу целевая для конвейера.
    ///
    /// Родные для ScreenCaptureKit 48 кГц стоили бы 0.64 ГБ на диск за час
    /// встречи (два канала s16) и лишнего ресемплинга в демоне. Просим 16 кГц
    /// у самой системы: 220 МБ/час на оба канала и ни одного пересчёта —
    /// STT всё равно работает на 16 кГц.
    static let sampleRate = 16_000

    /// Право «Запись экрана» выдано, пока приложение уже работало.
    ///
    /// macOS применяет его только к новому запуску процесса, поэтому сам факт
    /// выдачи ничего не меняет для текущей сессии — но человеку про это надо
    /// сказать, иначе он видит галочку в системных настройках и не понимает,
    /// почему собеседника по-прежнему нет в записи. Читает готовность.
    private(set) static var accessGrantedInThisSession = false

    /// Метка и отдельный каталог конкретного захвата.
    private let sessionID: UUID
    private let paths: SessionPaths

    private var stream: SCStream?
    private var sink: Sink?

    /// Пересоздание после `didStopWithError` (карточка №35).
    ///
    /// Поток пересоздаётся на ТЕ ЖЕ файлы: демон читает их хвостом и после
    /// паузы продолжит с последней позиции — ни манифест, ни демон менять не
    /// нужно. Пока идёт пересоздание, `isActive` остаётся true: с точки
    /// зрения встречи источник жив, просто молчит.
    private var restartPolicy = CaptureRestartPolicy()
    private var restartTask: Task<Void, Never>?
    private var stopping = false
    /// Захват потерян окончательно (исчерпаны попытки или человек дважды
    /// остановил поток сам). Аргументы — причина для строки статуса и
    /// признак «остановил человек»: сознательный «Стоп» в системном
    /// индикаторе — не сбой, встречу перезапускать не надо.
    var onCaptureLost: ((String, Bool) -> Void)?
    /// Поток пересоздан и снова отдаёт кадры. Аргумент — какой была ошибка.
    var onCaptureRecovered: ((String) -> Void)?

    override convenience init() {
        self.init(sessionID: UUID())
    }

    init(sessionID: UUID) {
        self.sessionID = sessionID
        self.paths = Self.sessionPaths(sessionID: sessionID)
        super.init()
    }

    var isActive: Bool { stream != nil || restartTask != nil }
    /// Идёт пересоздание после сбоя: сторожу приложения в это время
    /// перезапускать встречу по тишине аудиовхода незачем — демон жив,
    /// молчит источник, и он вот-вот вернётся или честно сдастся.
    var isRestarting: Bool { restartTask != nil }
    /// Сколько ждём системные вызовы ScreenCaptureKit при сборке потока:
    /// они не отменяются и таймаута не имеют, а подвисший сервис захвата —
    /// ровно тот сценарий, ради которого пересоздание и нужно (DS, круг-1).
    nonisolated static let openTimeout: UInt64 = 10_000_000_000

    /// Поднять захват. Возвращает false, если система отказала — вызывающий
    /// обязан откатиться на BlackHole, а не остаться без второй стороны.
    @discardableResult
    func start() async -> Bool {
        guard stream == nil else { return true }
        guard !Task.isCancelled else { return false }
        stopping = false
        restartPolicy = CaptureRestartPolicy()
        // Право то же, что у тапов: «Запись экрана и системного звука».
        // Проверяем ДО захвата: без него SCShareableContent бросает, а
        // человеку нужен внятный откат, а не тишина.
        if !CGPreflightScreenCaptureAccess() {
            // Диалог показываем, но на ответ не рассчитываем: macOS применяет
            // выданное право только к НОВОМУ запуску процесса. Человек нажимает
            // «Разрешить», видит галочку в системных настройках и уверен, что
            // готово, — а захват молча падает до конца сессии (аудит P0-10).
            // Поэтому запоминаем факт выдачи и говорим о перезапуске вслух:
            // готовность покажет это отдельной строкой.
            let granted = CGRequestScreenCaptureAccess()
            if granted || CGPreflightScreenCaptureAccess() {
                Self.accessGrantedInThisSession = true
                log("разрешение на системный звук выдано — начнёт действовать "
                  + "после перезапуска приложения; сейчас остаёмся на BlackHole")
            } else {
                log("нет разрешения на запись системного звука — остаёмся на BlackHole")
            }
            return false
        }

        var micInStream = false
        if #available(macOS 15.0, *) { micInStream = true }

        let sink = Sink(systemURL: paths.systemURL, micURL: paths.micURL) { [weak self] dead, error in
            // Очередь ScreenCaptureKit → главный актор: решение о пересоздании
            // живёт там же, где `stream` и `stop()`. Через границу идёт
            // идентификатор, а не сам SCStream: он не Sendable (Codex, круг-1).
            Task { @MainActor [weak self] in self?.streamDidStop(dead, error) }
        }
        let stream: SCStream
        do {
            stream = try await openStream(sink: sink)
        } catch {
            log("захват не стартовал: \(error.localizedDescription)")
            sink.close()
            cleanupSessionFiles()
            return false
        }
        guard !Task.isCancelled else {
            try? await stream.stopCapture()
            sink.close()
            cleanupSessionFiles()
            return false
        }
        self.stream = stream
        self.sink = sink

        // Манифест выписываем только ПОСЛЕ первых реальных кадров: его
        // наличие для демона означает «поток жив», а не «мы попытались».
        do {
            try await Task.sleep(nanoseconds: 1_200_000_000)
        } catch {
            await stop()
            return false
        }
        guard sink.systemFrames > 0 else {
            log("кадров нет за секунду — остаёмся на BlackHole")
            await stop()
            return false
        }
        writeManifest(micInStream: micInStream && sink.micFrames > 0)
        log("системный звук через ScreenCaptureKit: \(sink.systemFrames) кадров за секунду"
            + (sink.micFrames > 0 ? ", микрофон тем же потоком" : ", микрофон отдельно"))
        return true
    }

    /// Собрать и запустить поток под уже открытый приёмник — с потолком
    /// по времени.
    ///
    /// `SCShareableContent` и `startCapture` не отменяются и могут не
    /// вернуться, если сервис захвата подвис; без потолка `stop()`, ждущий
    /// цикл пересоздания, не заканчивался бы, и следующая встреча не
    /// стартовала бы. Гонка — на continuation с одноразовым резюмом: сборка
    /// и таймер — две независимые задачи на главном акторе, кто первый, тот
    /// и отвечает. Структурированная группа здесь не годится: `await
    /// work.value` в дочерней задаче не отменяется, и группа ждала бы
    /// зависший вызов до конца (круг-2 по PR #383, Codex). Поток, родившийся
    /// после таймаута, помечается отпущенным и гасится.
    private func openStream(sink: Sink) async throws -> SCStream {
        final class Once: @unchecked Sendable { var done = false }   // только с MainActor
        struct Box: @unchecked Sendable { let stream: SCStream }    // поток живёт на MainActor
        let once = Once()
        let workBox = TaskBox()
        let open: () async throws -> Box = {
            try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Box, Error>) in
                let work = Task { @MainActor [weak self] in
                    guard let self else {
                        guard !once.done else { return }
                        once.done = true
                        cont.resume(throwing: CancellationError())
                        return
                    }
                    do {
                        let fresh = try await self.openStreamNow(sink: sink)
                        if once.done {                    // опоздал к таймеру или к stop()
                            await self.retire(fresh)
                            return
                        }
                        once.done = true
                        cont.resume(returning: Box(stream: fresh))
                    } catch {
                        guard !once.done else { return }
                        once.done = true
                        cont.resume(throwing: error)
                    }
                }
                workBox.task = work
                Task { @MainActor in
                    try? await Task.sleep(nanoseconds: Self.openTimeout)
                    guard !once.done else { return }
                    once.done = true
                    work.cancel()
                    cont.resume(throwing: CaptureError.timeout)
                }
                // stop() отменил ожидание — резюмируем сразу, а не через таймер
                // (иначе «Стоп» ждал бы до 10 с зависший системный вызов).
                workBox.onCancel = {
                    guard !once.done else { return }
                    once.done = true
                    work.cancel()
                    cont.resume(throwing: CancellationError())
                }
            }
        }
        return try await withTaskCancellationHandler(operation: open) {
            Task { @MainActor in workBox.onCancel?() }
        }.stream
    }

    /// Коробка для задачи сборки и реакции на отмену: сама задача
    /// рождается внутри continuation, а обработчик отмены — снаружи.
    private final class TaskBox: @unchecked Sendable {
        var task: Task<Void, Never>?
        var onCancel: (@MainActor () -> Void)?
    }

    /// Одна и та же дорога для первого старта и для пересоздания: контент и
    /// дисплей запрашиваются заново каждый раз — после докинга или сна
    /// прежний `SCContentFilter` может указывать на исчезнувший дисплей.
    private func openStreamNow(sink: Sink) async throws -> SCStream {
        let content = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: false)
        try Task.checkCancellation()
        guard let display = content.displays.first else {
            throw CaptureError.noDisplay
        }
        // Себя исключаем дважды: фильтром и excludesCurrentProcessAudio.
        // Иначе собственные звуки приложения окажутся в записи встречи.
        let me = content.applications.first {
            $0.processID == ProcessInfo.processInfo.processIdentifier
        }
        let filter = SCContentFilter(display: display,
                                     excludingApplications: me.map { [$0] } ?? [],
                                     exceptingWindows: [])

        let cfg = SCStreamConfiguration()
        cfg.capturesAudio = true
        cfg.sampleRate = Self.sampleRate
        cfg.channelCount = 2
        cfg.excludesCurrentProcessAudio = true
        // Картинка не нужна, но поток обязан её иметь: минимальный кадр и
        // одна секунда между кадрами — это доли процента CPU.
        cfg.width = 2
        cfg.height = 2
        cfg.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        cfg.queueDepth = 6
        if #available(macOS 15.0, *) {
            cfg.captureMicrophone = true
        }

        let stream = SCStream(filter: filter, configuration: cfg, delegate: sink)
        let queue = DispatchQueue(label: "ai.charoite.sck", qos: .userInitiated)
        try stream.addStreamOutput(sink, type: .audio, sampleHandlerQueue: queue)
        if #available(macOS 15.0, *) {
            try stream.addStreamOutput(sink, type: .microphone, sampleHandlerQueue: queue)
        }
        try await stream.startCapture()
        return stream
    }

    enum CaptureError: LocalizedError {
        case noDisplay
        case timeout
        var errorDescription: String? {
            switch self {
            case .noDisplay: return "дисплеев нет — захват невозможен"
            case .timeout: return "сервис захвата не ответил за \(Self.seconds) с"
            }
        }
        private nonisolated static var seconds: Int { Int(SystemAudioCapture.openTimeout / 1_000_000_000) }
    }

    /// Поток остановился сам — не по `stop()`.
    ///
    /// Раньше это только логировалось, и на macOS 15 встреча до конца
    /// оставалась без собеседника и микрофона (они идут одним потоком).
    /// Теперь — пересоздание по политике `CaptureRestartPolicy`.
    private func streamDidStop(_ dead: ObjectIdentifier, _ error: Error) {
        // Запоздалый сигнал от уже отпущенного потока — не про нас: делегат
        // один на все потоки сессии, отпущенные помним по идентификатору.
        guard !stopping, !retired.contains(dead) else { return }
        let ns = error as NSError
        log("поток остановлен не нами (\(ns.domain) \(ns.code): "
          + "\(error.localizedDescription)) — пересоздаю")
        if restartTask != nil {
            // Свежий поток (возможно, ещё не присвоенный в `stream` — окно
            // между startCapture и возвратом из openStream) умер, пока цикл
            // ждал кадров: цикл увидит это сам и пойдёт на следующую
            // попытку с верным признаком «Стоп человека», а не объявит победу.
            // Запоминаем, ЧЕЙ это сигнал: опоздавший поток прошлой попытки
            // не должен списывать здоровый текущий (Codex, круг-3).
            stopDuringRestart = (dead, error)
            return
        }
        guard let current = stream, ObjectIdentifier(current) == dead else { return }
        let reason = error.localizedDescription
        restartTask = Task { @MainActor [weak self] in
            await self?.restartLoop(userStopped: Self.isUserStop(error), original: reason)
        }
    }

    /// Частота микрофона у нового потока своя: за паузу могло смениться
    /// устройство входа. Первый буфер микрофона может прийти позже секунды
    /// проверки — тогда смотрим ещё раз через несколько секунд. Манифест
    /// переписываем, чтобы демон после перезапуска читал правильную; живой
    /// демон манифест не перечитывает — расхождение говорим вслух.
    private func syncMicRate(of fresh: SCStream, sink: Sink, attempt: Int = 0) {
        guard let rate = sink.micRate(of: fresh) else {
            if attempt < 3 {
                Task { @MainActor [weak self] in
                    try? await Task.sleep(nanoseconds: 3_000_000_000)
                    guard let self, self.stream === fresh else { return }
                    self.syncMicRate(of: fresh, sink: sink, attempt: attempt + 1)
                }
            }
            return
        }
        if rate != sink.micSampleRate {
            log("микрофон после пересоздания \(rate) Гц вместо \(sink.micSampleRate) — "
              + "демон узнает после перезапуска")
            sink.adoptMicRate(rate)
            writeManifest(micInStream: true)
        }
    }

    private var stopDuringRestart: (ObjectIdentifier, Error)?
    /// Потоки, которые мы уже отпустили: их сигналы больше не считаются.
    private var retired: Set<ObjectIdentifier> = []

    /// Отпустить поток: запомнить, забыть его счётчики, остановить.
    /// Карты по ObjectIdentifier не растут за долгую встречу с флапающим
    /// захватом, а переиспользованный адрес не наследует чужие кадры.
    private func retire(_ dead: SCStream) async {
        let id = ObjectIdentifier(dead)
        retired.insert(id)
        sink?.forget(id)
        try? await dead.stopCapture()
    }

    private static func isUserStop(_ error: Error) -> Bool {
        let ns = error as NSError
        return ns.domain == SCStreamErrorDomain
            && ns.code == SCStreamError.Code.userStopped.rawValue
    }

    private func restartLoop(userStopped firstUserStopped: Bool, original: String) async {
        defer { restartTask = nil }
        var userStopped = firstUserStopped
        while !stopping, !Task.isCancelled {
            switch restartPolicy.decide(userStopped: userStopped, now: Date()) {
            case .giveUp(let reason):
                log("захват не восстановлен: \(reason)")
                if !stopping { onCaptureLost?(reason, userStopped) }
                return
            case .retry(let delay):
                do {
                    try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                } catch {
                    return                                  // stop() отменил ожидание
                }
            }
            guard !stopping, !Task.isCancelled, let sink else { return }
            // Старый поток отпускаем (он уже мёртв), новый — на те же файлы.
            if let dead = stream {
                stream = nil
                await retire(dead)
            }
            // Флаг «свежий умер» взводится ДО сборки: сигнал нового потока
            // может прийти раньше, чем openStream вернёт его нам (Codex).
            stopDuringRestart = nil
            let fresh: SCStream
            do {
                fresh = try await openStream(sink: sink)
            } catch {
                log("пересоздание не удалось: \(error.localizedDescription)")
                userStopped = false
                continue
            }
            if stopping || Task.isCancelled {
                try? await fresh.stopCapture()              // stop() успел раньше
                return
            }
            stream = fresh
            let freshID = ObjectIdentifier(fresh)
            sink.adopt(freshID)
            retired.remove(freshID)
            if stopDuringRestart?.0 != freshID {
                try? await Task.sleep(nanoseconds: 1_200_000_000)
            }
            if stopping || Task.isCancelled { return }
            if let (who, again) = stopDuringRestart, who == freshID {
                stopDuringRestart = nil
                log("свежий поток умер, не успев начать — ещё попытка")
                stream = nil
                await retire(fresh)
                userStopped = Self.isUserStop(again)
                continue
            }
            stopDuringRestart = nil               // чужой сигнал — не про нас
            // Кадры считаем ИМЕННО нового потока: общий счётчик засчитал бы
            // хвост буферов мёртвого, долетевший на свою очередь позже.
            if sink.frames(of: fresh) > 0 {
                restartPolicy.recovered()
                log("захват пересоздан, кадры идут (было: \(original))")
                // Частота микрофона — у нового потока своя: за паузу могло
                // смениться устройство входа. Манифест переписываем, чтобы
                // демон после перезапуска читал правильную; живой демон
                // манифест не перечитывает — расхождение говорим вслух.
                syncMicRate(of: fresh, sink: sink)
                onCaptureRecovered?(original)
                return
            }
            log("после пересоздания кадров нет — ещё попытка")
            stream = nil
            await retire(fresh)
            userStopped = false
        }
    }

    func stop() async {
        stopping = true
        restartTask?.cancel()
        _ = await restartTask?.value              // не оставить поток, созданный после stop()
        restartTask = nil
        if let stream {
            try? await stream.stopCapture()
        }
        stream = nil
        sink?.close()
        sink = nil

        // Общий манифест удаляем только пока он указывает на нас. Каталог
        // сессии уникален, поэтому его можно убрать независимо от уже
        // стартовавшей следующей встречи.
        if Self.manifestSession() == sessionID.uuidString {
            try? FileManager.default.removeItem(at: Self.manifestURL)
        } else {
            log("файлы принадлежат новой встрече — не трогаем")
        }
        cleanupSessionFiles()
    }

    /// Идентификатор сессии из манифеста на диске; nil — манифеста нет или он
    /// от версии без поля (тогда владение недоказуемо и удалять нельзя).
    static func manifestSession() -> String? {
        guard let data = try? Data(contentsOf: manifestURL),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }
        return json["session"] as? String
    }

    private func writeManifest(micInStream: Bool) {
        var manifest: [String: Any] = [
            "engine": "screencapturekit",
            "samplerate": Self.sampleRate,      // системный поток
            "format": "s16le",
            "system": paths.systemURL.path,
            "system_rate": Self.sampleRate,
            // Кто именно владеет этими файлами прямо сейчас. Демон поле
            // игнорирует, а нам оно нужно при остановке — см. `stop()`.
            "session": sessionID.uuidString,
        ]
        if micInStream {
            manifest["mic"] = paths.micURL.path
            // Частота микрофона — фактическая, а не запрошенная.
            manifest["mic_rate"] = sink?.micSampleRate ?? Self.sampleRate
        }
        guard let data = try? JSONSerialization.data(withJSONObject: manifest) else { return }
        FileManager.default.createPrivateDirectory(at: Self.dir)
        try? data.write(to: Self.manifestURL, options: .atomic)
    }

    private func cleanupSessionFiles() {
        try? FileManager.default.removeItem(at: paths.directory)
    }

    private func log(_ message: String) {
        NSLog("[SystemAudioCapture] %@", message)
    }

    // MARK: - Приёмник кадров
    //
    // Живёт вне главного актора: ScreenCaptureKit зовёт с своей очереди, и
    // прыжок на MainActor на каждом буфере — это дропы на ровном месте.

    private final class Sink: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
        private let systemHandle: FileHandle?
        private let micHandle: FileHandle?
        /// Счётчики и частота пишутся на очереди SCStream, читаются с
        /// MainActor (`start`, `writeManifest`): без замка это гонка данных
        /// по модели памяти Swift — на arm64 практически безобидная, но
        /// `@unchecked Sendable` её лишь прятал (аудит DeepSeek 16.08, две
        /// зоны независимо). Тот же замок закрывает файлы: буфер, пришедший
        /// с очереди после `close()`, писал в закрытый FileHandle —
        /// ObjC-исключение и падение приложения на остановке.
        private struct Counters {
            var systemFrames = 0
            var micFrames = 0
            var micSampleRate = 0
            var closed = false
            /// Кадры и частота микрофона по потоку: после пересоздания
            /// «пошли ли кадры» спрашивается у нового, а не у суммы.
            var framesByStream: [ObjectIdentifier: Int] = [:]
            var micRateByStream: [ObjectIdentifier: Int] = [:]
            /// Отпущенные потоки: их поздние буферы не пишутся и не
            /// считаются — иначе forget() не окончателен, а адрес,
            /// переиспользованный новым потоком, наследовал бы чужие кадры.
            var tombstones: Set<ObjectIdentifier> = []
        }
        private let state = OSAllocatedUnfairLock(initialState: Counters())
        /// Поток остановился сам; зовётся с очереди ScreenCaptureKit.
        /// Первый аргумент — какой именно поток: делегат общий для всех
        /// потоков сессии, и запоздалый сигнал мёртвого не должен
        /// ронять живой.
        private let onStop: @Sendable (ObjectIdentifier, Error) -> Void

        var systemFrames: Int { state.withLock { $0.systemFrames } }
        var micFrames: Int { state.withLock { $0.micFrames } }
        /// Реальная частота микрофона. ScreenCaptureKit применяет
        /// SCStreamConfiguration.sampleRate только к системному звуку —
        /// микрофон приходит в родном формате устройства (обычно 48 кГц).
        /// Проверено живым тестом 07.08: файл микрофона рос втрое быстрее
        /// системного, и демон растянул бы его по частоте из манифеста.
        var micSampleRate: Int { state.withLock { $0.micSampleRate } }
        func frames(of stream: SCStream) -> Int {
            let id = ObjectIdentifier(stream)
            return state.withLock { $0.framesByStream[id] ?? 0 }
        }
        func micRate(of stream: SCStream) -> Int? {
            let id = ObjectIdentifier(stream)
            return state.withLock { $0.micRateByStream[id] }
        }
        func adoptMicRate(_ rate: Int) {
            state.withLock { $0.micSampleRate = rate }
        }
        func forget(_ id: ObjectIdentifier) {
            state.withLock { st in
                st.framesByStream[id] = nil
                st.micRateByStream[id] = nil
                st.tombstones.insert(id)
            }
        }
        /// Новый поток занял адрес отпущенного — снять надгробие.
        func adopt(_ id: ObjectIdentifier) {
            state.withLock { _ = $0.tombstones.remove(id) }
        }

        init(systemURL: URL, micURL: URL,
             onStop: @escaping @Sendable (ObjectIdentifier, Error) -> Void) {
            self.onStop = onStop
            let fm = FileManager.default
            // 0700/0600: сырой звук встречи не должен читаться другими
            // учётками машины (аудит 16.08, см. PrivateFiles)
            fm.createPrivateDirectory(at: systemURL.deletingLastPathComponent())
            for url in [systemURL, micURL] { fm.createPrivateFile(atPath: url.path) }
            systemHandle = try? FileHandle(forWritingTo: systemURL)
            micHandle = try? FileHandle(forWritingTo: micURL)
        }

        func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer,
                    of type: SCStreamOutputType) {
            guard sb.isValid else { return }
            let sid = ObjectIdentifier(stream)      // в замок — идентификатор, не поток
            switch type {
            case .audio:
                if let pcm = mono(from: sb) {
                    state.withLock { st in
                        guard !st.closed, !st.tombstones.contains(sid) else { return }
                        st.systemFrames += pcm.count
                        st.framesByStream[sid, default: 0] += pcm.count
                        write(pcm, to: systemHandle)
                    }
                }
            case .microphone:
                let rate = sb.formatDescription?.audioStreamBasicDescription?.mSampleRate
                let pcm = mono(from: sb)
                state.withLock { st in
                    guard !st.closed, !st.tombstones.contains(sid) else { return }
                    if st.micSampleRate == 0, let rate { st.micSampleRate = Int(rate) }
                    if let rate, st.micRateByStream[sid] == nil {
                        st.micRateByStream[sid] = Int(rate)
                    }
                    if let pcm {
                        st.micFrames += pcm.count
                        write(pcm, to: micHandle)
                    }
                }
            default:
                break
            }
        }

        func stream(_ stream: SCStream, didStopWithError error: Error) {
            NSLog("[SystemAudioCapture] поток остановлен: %@", error.localizedDescription)
            onStop(ObjectIdentifier(stream), error)
        }

        /// float32-кадры любого числа каналов → моно s16le.
        ///
        /// Сводим здесь, а не в демоне: конвейер всё равно работает в моно,
        /// а по файлу так едет вдвое меньше данных.
        private func mono(from sb: CMSampleBuffer) -> [Int16]? {
            try? sb.withAudioBufferList { list, _ -> [Int16]? in
                guard let desc = sb.formatDescription?.audioStreamBasicDescription,
                      let format = AVAudioFormat(standardFormatWithSampleRate: desc.mSampleRate,
                                                 channels: desc.mChannelsPerFrame),
                      let buf = AVAudioPCMBuffer(pcmFormat: format,
                                                 bufferListNoCopy: list.unsafePointer),
                      let data = buf.floatChannelData
                else { return nil }
                let frames = Int(buf.frameLength)
                let channels = Int(buf.format.channelCount)
                guard frames > 0, channels > 0 else { return nil }
                var out = [Int16](repeating: 0, count: frames)
                for i in 0..<frames {
                    var sum: Float = 0
                    for ch in 0..<channels { sum += data[ch][i] }
                    let v = sum / Float(channels)
                    // isFinite обязателен: Int16(NaN) — краш аудио-нити.
                    out[i] = Int16((v.isFinite ? max(-1, min(1, v)) : 0) * 32767)
                }
                return out
            }
        }

        /// Вызывать только под `state`: так запись и `close()` не пересекаются.
        private func write(_ pcm: [Int16], to handle: FileHandle?) {
            guard let handle else { return }
            // Бросающий API вместо `write(_:)`: ошибка записи (диск кончился,
            // дескриптор закрыт) — ошибка Swift, а не ObjC-исключение,
            // которое роняет процесс целиком.
            pcm.withUnsafeBufferPointer { try? handle.write(contentsOf: Data(buffer: $0)) }
        }

        func close() {
            state.withLock { st in
                guard !st.closed else { return }
                st.closed = true
                try? systemHandle?.close()
                try? micHandle?.close()
            }
        }
    }
}

#endif
