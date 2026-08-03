import Foundation

#if os(macOS)

/// Длительность встречи для строки списка — с кэшем по (встреча, updatedAt).
///
/// Длительность считается по таймкодам стенограммы, а список перерисовывается
/// на каждом тике конвейера: без кэша двадцать строк означали двадцать чтений
/// файлов на каждое обновление статуса. Ключ включает updatedAt: доработка
/// встречи (повтор, ревизия) двигает штамп — и только тогда файл читается
/// заново.
@MainActor
enum MeetingDurationCache {
    private static var cache: [String: (stamp: TimeInterval, text: String?)] = [:]

    static func durationText(for meeting: MeetingProcessingSnapshot) -> String? {
        if let hit = cache[meeting.meetingID], hit.stamp == meeting.updatedAt {
            return hit.text
        }
        let text = (try? String(contentsOfFile: meeting.transcriptPath, encoding: .utf8))
            .flatMap { MeetingCardLoader.durationText(fromTranscript: $0) }
        cache[meeting.meetingID] = (meeting.updatedAt, text)
        return text
    }

    /// Для тестов: чистый старт между кейсами.
    static func reset() { cache.removeAll() }
}
#endif
