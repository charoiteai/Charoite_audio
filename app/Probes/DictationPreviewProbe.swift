import AVFoundation
import XCTest
@testable import CharoiteApp

/// Живая проба черновика диктовки: эталонный wav подаётся кусками, как
/// микрофон, через тот же конвертер и поток, что и в приложении.
///
/// Не гейт — наблюдение: нужны macOS 26 и установленные ассеты языка.
///
///   CHAROITE_DICTATION_PROBE=/путь/к/ref.wav \
///     swift test --package-path app \
///       --filter CharoiteAppLiveProbes.DictationPreviewProbe
final class DictationPreviewProbe: XCTestCase {
    func testStreamedWavProducesDraft() async throws {
        guard let path = ProcessInfo.processInfo.environment["CHAROITE_DICTATION_PROBE"],
              !path.isEmpty else { throw XCTSkip("не запрошено") }
        guard #available(macOS 26.0, *) else { throw XCTSkip("нужна macOS 26") }

        let file = try AVAudioFile(forReading: URL(fileURLWithPath: path))
        let live = LiveDictationPreview(locale: Locale(identifier: "ru-RU"))
        let ready = await live.prepare()
        guard ready == .ready else { throw XCTSkip("движок не готов: \(ready)") }

        var updates = 0
        live.onChange = { _ in updates += 1 }
        try await live.start(inputFormat: file.processingFormat)

        // Кусками по 4096 кадров — как отдаёт tap микрофона.
        let started = Date()
        while file.framePosition < file.length {
            guard let buf = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: 4096) else { break }
            try file.read(into: buf, frameCount: 4096)
            if buf.frameLength == 0 { break }
            live.ingest(buf)
        }
        let text = await live.finish()
        let seconds = Date().timeIntervalSince(started)
        print("черновик (\(updates) обновлений, \(String(format: "%.1f", seconds)) с): \(text)")
        XCTAssertFalse(text.isEmpty, "поток дошёл до движка, а текста нет")
        XCTAssertGreaterThan(updates, 1, "живых обновлений не было — черновик не живой")
    }
}
