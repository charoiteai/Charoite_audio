import AppIntents
import Foundation

/// «Начать запись в Charoite» — Siri, Команды, кнопка действия, Back Tap.
///
/// iOS не запускает запись из фона: сессия стартует только у приложения на
/// экране, и никакой таймер или геозона этого не обходят. Поэтому интент
/// открывает приложение (`openAppWhenRun`) и просит рекордер стартовать;
/// экран записи подхватывает просьбу первым делом — даже если сам ещё не
/// успел появиться к моменту `perform()`.
@available(iOS 17.0, *)
struct StartRecordingIntent: AppIntent {
    static var title: LocalizedStringResource = "Start recording"
    static var description = IntentDescription("Opens Charoite and starts recording a meeting.")
    static var openAppWhenRun = true

    init() {}

    @MainActor
    func perform() async throws -> some IntentResult {
        RecordingControl.requestStart()
        return .result()
    }
}

/// Фразы для Siri и готовая карточка в Командах — без настройки руками.
@available(iOS 17.0, *)
struct CharoiteShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: StartRecordingIntent(),
            phrases: [
                "Start recording in \(.applicationName)",
                "Record a meeting in \(.applicationName)",
                "Начать запись в \(.applicationName)",
                "Запиши встречу в \(.applicationName)",
                "用\(.applicationName)开始录音",
            ],
            shortTitle: "Start recording",
            systemImageName: "record.circle")
    }
}
