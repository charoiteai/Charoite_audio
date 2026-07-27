import AVFoundation
import Foundation

/// Запись m4a с продолжением в фоне (Background Audio).
///
/// Правила платформы: стартуем ТОЛЬКО с экрана (из фона iOS не даст),
/// прерывание звонком ловим и по возможности возобновляем. Файл по стопу
/// уезжает в iCloud-папку импорта — дальше всё делает Mac.
@MainActor
final class Recorder: NSObject, ObservableObject {
    enum Kind: String, CaseIterable, Identifiable {
        case meeting = "Встреча"
        case note = "Заметка"
        case diary = "Дневник"
        var id: String { rawValue }
        /// Префикс имени файла — по нему Mac выбирает конвейер.
        var prefix: String {
            switch self {
            case .meeting: return ""
            case .note: return "note_"
            case .diary: return "diary_"
            }
        }
    }

    @Published var isRecording = false
    @Published var elapsed: TimeInterval = 0
    @Published var level: Float = 0          // 0…1 для волны
    @Published var lastResult: String?       // статус доставки/очереди

    private var recorder: AVAudioRecorder?
    private var timer: Timer?
    private var startedAt: Date?

    func start(kind: Kind) {
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playAndRecord, mode: .default,
                                    options: [.allowBluetooth])
            try session.setActive(true)
        } catch {
            lastResult = "Микрофон не дали: \(error.localizedDescription)"
            return
        }
        let stamp = Self.stamp(Date())
        let name = "\(kind.prefix)iphone_\(stamp).m4a"
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(name)
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 44_100,
            AVNumberOfChannelsKey: 1,
            AVEncoderBitRateKey: 96_000,
        ]
        do {
            let r = try AVAudioRecorder(url: url, settings: settings)
            r.isMeteringEnabled = true
            r.record()
            recorder = r
            startedAt = Date()
            isRecording = true
            lastResult = nil
            timer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in
                Task { @MainActor [weak self] in self?.tick() }
            }
        } catch {
            lastResult = "Запись не стартовала: \(error.localizedDescription)"
        }
    }

    func stop() {
        guard let r = recorder else { return }
        r.stop()
        timer?.invalidate()
        timer = nil
        isRecording = false
        let url = r.url
        recorder = nil
        Task { await Inbox.deliver(url) { [weak self] msg in self?.lastResult = msg } }
    }

    private func tick() {
        guard let r = recorder, let t0 = startedAt else { return }
        elapsed = Date().timeIntervalSince(t0)
        r.updateMeters()
        // −60…0 дБ → 0…1
        level = max(0, min(1, (r.averagePower(forChannel: 0) + 60) / 60))
    }

    static func stamp(_ d: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd_HHmm"
        f.locale = Locale(identifier: "en_US_POSIX")
        return f.string(from: d)
    }
}
