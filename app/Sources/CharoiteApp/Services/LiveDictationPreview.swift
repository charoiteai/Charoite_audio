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

    /// Вход анализатора: конвертер под формат микрофона и поток кадров.
    /// Один неизменяемый контейнер на сессию — `ingest` с аудио-нити берёт
    /// его под замком, `finish`/`cancel` под тем же замком снимают.
    private final class Intake {
        let converter: AVAudioConverter
        let target: AVAudioFormat
        let feed: AsyncStream<AnalyzerInput>.Continuation
        init(converter: AVAudioConverter, target: AVAudioFormat,
             feed: AsyncStream<AnalyzerInput>.Continuation) {
            self.converter = converter; self.target = target; self.feed = feed
        }
    }

    private let transcriber: DictationTranscriber
    private var analyzer: SpeechAnalyzer?
    private var intake: Intake?
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

    /// Готов ли движок к этому языку. Ассеты приложение НЕ качает: это был
    /// бы поход в сеть мимо рубильника `CHAROITE_NO_CLOUD` и мимо обещания
    /// PRIVACY.md «приложение не скачивает модели». Язык диктовки ставится
    /// системой (Системные настройки → Клавиатура → Диктовка), до тех пор
    /// диктовка идёт как раньше, без черновика.
    func prepare() async -> Availability {
        switch await AssetInventory.status(forModules: [transcriber]) {
        case .installed: return .ready
        case .unsupported: return .unsupported
        default: return .assetsMissing
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
        let (stream, continuation) = AsyncStream<AnalyzerInput>.makeStream()
        lock.withLock { intake = Intake(converter: conv, target: wanted, feed: continuation) }
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
        guard let intake = lock.withLock({ intake }) else { return }
        let ratio = intake.target.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 16
        guard let out = AVAudioPCMBuffer(pcmFormat: intake.target, frameCapacity: capacity) else { return }
        var consumed = false
        var error: NSError?
        let status = intake.converter.convert(to: out, error: &error) { _, outStatus in
            if consumed { outStatus.pointee = .noDataNow; return nil }
            consumed = true
            outStatus.pointee = .haveData
            return buffer
        }
        guard status != .error, out.frameLength > 0 else { return }
        intake.feed.yield(AnalyzerInput(buffer: out))
    }

    /// Снять вход: кадры после этого не принимаются, поток закрыт.
    private func closeIntake() {
        let closed: Intake? = lock.withLock {
            let current = intake
            intake = nil
            return current
        }
        closed?.feed.finish()
    }

    /// Закрыть вход и дождаться последних результатов. Возвращает черновик.
    func finish() async -> String {
        closeIntake()
        try? await analyzer?.finalizeAndFinishThroughEndOfInput()
        await results?.value
        results = nil
        analyzer = nil
        return text
    }

    /// Бросить без ожидания — старт не удался или диктовка отменена.
    func cancel() {
        closeIntake()
        results?.cancel()
        results = nil
        let analyzer = self.analyzer
        self.analyzer = nil
        Task { await analyzer?.cancelAndFinishNow() }
    }
}

#endif
