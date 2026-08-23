import Foundation

/// Что делать, когда системный захват (ScreenCaptureKit) остановился сам.
///
/// `stream(_:didStopWithError:)` приходит, когда поток умер не по нашей
/// команде: человек нажал «Стоп» в системном индикаторе записи экрана
/// (`SCStreamErrorUserStopped`, −3817); система сама остановила поток — сон,
/// смена дисплеев, блокировка (`SCStreamErrorSystemStoppedStream`, −3821,
/// macOS 15+); оборвалось соединение с сервисом захвата (−3804/−3805).
/// С macOS 15 тем же потоком приходит и микрофон, поэтому без пересоздания
/// встреча до конца оставалась без обеих сторон, а сторож демона
/// перезапускал каналы вхолостую (аудит DeepSeek 16.08, I1; карточка №35).
///
/// Политика — чистая, без ScreenCaptureKit, чтобы её держал тест:
/// экспоненциальная пауза между попытками, потолок попыток и уважение к
/// человеку — два «Стоп» подряд значат «стоп».
struct CaptureRestartPolicy: Equatable {
    enum Decision: Equatable {
        case retry(after: TimeInterval)
        case giveUp(reason: String)
    }

    /// 2+4+8+16+32+60+60+60 с ≈ четыре минуты: столько живёт смена дисплея
    /// при докинге и пробуждение после сна; дольше — это не сбой, а режим.
    static let maxAttempts = 8
    static let maxDelay: TimeInterval = 60
    /// Второй «Стоп» человека за это время — честный, а не случайный клик.
    static let userStopWindow: TimeInterval = 120

    private(set) var attempts = 0
    private(set) var lastUserStop: Date?

    mutating func decide(userStopped: Bool, now: Date) -> Decision {
        if userStopped {
            if let last = lastUserStop, now.timeIntervalSince(last) < Self.userStopWindow {
                return .giveUp(reason: "остановлено человеком второй раз подряд")
            }
            lastUserStop = now
            return .retry(after: 1)
        }
        attempts += 1
        if attempts > Self.maxAttempts {
            return .giveUp(reason: "\(Self.maxAttempts) попыток подряд без кадров")
        }
        return .retry(after: min(Self.maxDelay, pow(2, Double(attempts))))
    }

    /// Поток снова отдаёт кадры — счёт попыток с нуля; память о «Стопе»
    /// человека остаётся: его второе нажатие в окне всё равно уважаем.
    mutating func recovered() {
        attempts = 0
    }
}
