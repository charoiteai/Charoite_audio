import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

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

    override convenience init() {
        self.init(sessionID: UUID())
    }

    init(sessionID: UUID) {
        self.sessionID = sessionID
        self.paths = Self.sessionPaths(sessionID: sessionID)
        super.init()
    }

    var isActive: Bool { stream != nil }

    /// Поднять захват. Возвращает false, если система отказала — вызывающий
    /// обязан откатиться на BlackHole, а не остаться без второй стороны.
    @discardableResult
    func start() async -> Bool {
        guard stream == nil else { return true }
        guard !Task.isCancelled else { return false }
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

        let content: SCShareableContent
        do {
            content = try await SCShareableContent.excludingDesktopWindows(
                false, onScreenWindowsOnly: false)
        } catch {
            log("контент недоступен: \(error.localizedDescription)")
            return false
        }
        guard !Task.isCancelled else { return false }
        guard let display = content.displays.first else {
            log("дисплеев нет — захват невозможен")
            return false
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
        var micInStream = false
        if #available(macOS 15.0, *) {
            cfg.captureMicrophone = true
            micInStream = true
        }

        let sink = Sink(systemURL: paths.systemURL, micURL: paths.micURL)
        let stream = SCStream(filter: filter, configuration: cfg, delegate: sink)
        let queue = DispatchQueue(label: "ai.charoite.sck", qos: .userInitiated)
        do {
            try stream.addStreamOutput(sink, type: .audio, sampleHandlerQueue: queue)
            if #available(macOS 15.0, *) {
                try stream.addStreamOutput(sink, type: .microphone, sampleHandlerQueue: queue)
            }
            try await stream.startCapture()
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

    func stop() async {
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
        try? FileManager.default.createDirectory(at: Self.dir, withIntermediateDirectories: true)
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
        private(set) var systemFrames = 0
        private(set) var micFrames = 0
        /// Реальная частота микрофона. ScreenCaptureKit применяет
        /// SCStreamConfiguration.sampleRate только к системному звуку —
        /// микрофон приходит в родном формате устройства (обычно 48 кГц).
        /// Проверено живым тестом 07.08: файл микрофона рос втрое быстрее
        /// системного, и демон растянул бы его по частоте из манифеста.
        private(set) var micSampleRate = 0

        init(systemURL: URL, micURL: URL) {
            let fm = FileManager.default
            try? fm.createDirectory(at: systemURL.deletingLastPathComponent(),
                                    withIntermediateDirectories: true)
            for url in [systemURL, micURL] { fm.createFile(atPath: url.path, contents: nil) }
            systemHandle = try? FileHandle(forWritingTo: systemURL)
            micHandle = try? FileHandle(forWritingTo: micURL)
        }

        func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer,
                    of type: SCStreamOutputType) {
            guard sb.isValid else { return }
            switch type {
            case .audio:
                if let pcm = mono(from: sb) {
                    systemFrames += pcm.count
                    write(pcm, to: systemHandle)
                }
            case .microphone:
                if micSampleRate == 0,
                   let rate = sb.formatDescription?.audioStreamBasicDescription?.mSampleRate {
                    micSampleRate = Int(rate)
                }
                if let pcm = mono(from: sb) {
                    micFrames += pcm.count
                    write(pcm, to: micHandle)
                }
            default:
                break
            }
        }

        func stream(_ stream: SCStream, didStopWithError error: Error) {
            NSLog("[SystemAudioCapture] поток остановлен: %@", error.localizedDescription)
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

        private func write(_ pcm: [Int16], to handle: FileHandle?) {
            guard let handle else { return }
            pcm.withUnsafeBufferPointer { handle.write(Data(buffer: $0)) }
        }

        func close() {
            try? systemHandle?.close()
            try? micHandle?.close()
        }
    }
}

#endif
