import AVFoundation
import Foundation

#if canImport(Speech)
import Speech
#endif

/// Сборка черновика из результатов диктовки: подтверждённые куски идут
/// подряд, «плавающий» хвост (volatile) показывается один и заменяется
/// следующим результатом того же места. Чистая логика — отдельно от Speech,
/// чтобы её проверял тест без микрофона и без macOS 26.
struct DictationDraft: Equatable {
    private(set) var finals: [String] = []
    private(set) var volatile = ""

    /// Результат движка: `isFinal` — кусок больше не изменится.
    mutating func apply(_ piece: String, isFinal: Bool) {
        let trimmed = piece.trimmingCharacters(in: .whitespacesAndNewlines)
        if isFinal {
            volatile = ""
            if !trimmed.isEmpty { finals.append(trimmed) }
        } else {
            volatile = trimmed
        }
    }

    var text: String {
        (finals + [volatile]).filter { !$0.isEmpty }.joined(separator: " ")
    }
}

#if canImport(Speech)

/// Живой черновик диктовки нативным движком — `DictationTranscriber`
/// (SpeechAnalyzer, macOS 26 / iOS 26, целиком на устройстве).
///
/// Это не замена GigaAM: на эталоне 02.09.2026 диктовка системы дала 12,4 %
/// ошибок против 2,9 %, теряет отраслевые аббревиатуры и не ставит знаков.
/// Зато текст появляется по ходу речи и без модели в 700 МБ — поэтому роль
/// ровно одна: показать человеку, что его слышат, и подстраховать, когда
/// GigaAM не ответил. Финальный текст всегда за GigaAM.
///
/// Платформенно-нейтральный: тот же класс возьмёт iOS-компаньон для
/// чернового текста на телефоне (№82) — там нужен второй захват рядом с
/// `AVAudioRecorder` и прогон на реальном устройстве.
@available(macOS 26.0, iOS 26.0, *)
final class LiveDictationPreview: @unchecked Sendable {
    enum Availability { case ready, assetsMissing, unsupported }

    private let transcriber: DictationTranscriber
    private var analyzer: SpeechAnalyzer?
    private var feed: AsyncStream<AnalyzerInput>.Continuation?
    private var converter: AVAudioConverter?
    private var target: AVAudioFormat?
    private var results: Task<Void, Never>?
    private let lock = NSLock()
    private var draft = DictationDraft()

    /// Полный текст черновика после каждого изменения — на главном потоке.
    var onChange: (@MainActor (String) -> Void)?

    init(locale: Locale) {
        transcriber = DictationTranscriber(
            locale: locale,
            contentHints: [],
            transcriptionOptions: [],
            reportingOptions: [.volatileResults],
            attributeOptions: [])
    }

    var text: String { lock.withLock { draft.text } }

    /// Готов ли движок к этому языку. Ассеты, которых нет, ставятся на
    /// загрузку в фоне — предпросмотр включится со следующей диктовки, а
    /// эта идёт как раньше, без черновика.
    func prepare() async -> Availability {
        let status = await AssetInventory.status(forModules: [transcriber])
        switch status {
        case .installed:
            return .ready
        case .unsupported:
            return .unsupported
        default:
            if let request = try? await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
                Task.detached { try? await request.downloadAndInstall() }
            }
            return .assetsMissing
        }
    }

    /// Поднять анализатор под формат микрофона. Кадры дальше — в `ingest`.
    func start(inputFormat: AVAudioFormat) async throws {
        guard let wanted = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else {
            throw NSError(domain: "LiveDictationPreview", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "анализатор не назвал формат звука"])
        }
        guard let conv = AVAudioConverter(from: inputFormat, to: wanted) else {
            throw NSError(domain: "LiveDictationPreview", code: 2,
                          userInfo: [NSLocalizedDescriptionKey: "нет конвертера \(inputFormat) → \(wanted)"])
        }
        target = wanted
        converter = conv
        let (stream, continuation) = AsyncStream<AnalyzerInput>.makeStream()
        feed = continuation
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        self.analyzer = analyzer
        let transcriber = self.transcriber
        results = Task { [weak self] in
            do {
                for try await result in transcriber.results {
                    guard let self else { return }
                    let piece = String(result.text.characters)
                    let full: String = self.lock.withLock {
                        self.draft.apply(piece, isFinal: result.isFinal)
                        return self.draft.text
                    }
                    if let onChange = self.onChange {
                        await MainActor.run { onChange(full) }
                    }
                }
            } catch {
                // Поток результатов оборвался — черновик просто перестаёт
                // расти; диктовка (GigaAM) от этого не зависит.
            }
        }
        try await analyzer.start(inputSequence: stream)
    }

    /// Вызывается с аудио-нити: перевод в формат анализатора и подача.
    func ingest(_ buffer: AVAudioPCMBuffer) {
        guard let converter, let target, let feed else { return }
        let ratio = target.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 16
        guard let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity) else { return }
        var consumed = false
        var error: NSError?
        let status = converter.convert(to: out, error: &error) { _, outStatus in
            if consumed { outStatus.pointee = .noDataNow; return nil }
            consumed = true
            outStatus.pointee = .haveData
            return buffer
        }
        guard status != .error, out.frameLength > 0 else { return }
        feed.yield(AnalyzerInput(buffer: out))
    }

    /// Закрыть вход и дождаться последних результатов. Возвращает черновик.
    func finish() async -> String {
        feed?.finish()
        feed = nil
        try? await analyzer?.finalizeAndFinishThroughEndOfInput()
        await results?.value
        results = nil
        analyzer = nil
        return text
    }

    /// Бросить без ожидания — старт не удался или диктовка отменена.
    func cancel() {
        feed?.finish()
        feed = nil
        results?.cancel()
        results = nil
        let analyzer = self.analyzer
        self.analyzer = nil
        Task { await analyzer?.cancelAndFinishNow() }
    }
}

#endif
