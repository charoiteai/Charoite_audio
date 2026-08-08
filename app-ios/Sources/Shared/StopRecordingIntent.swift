import AppIntents
import Foundation

/// Стоп прямо из Live Activity — с локскрина и из Dynamic Island.
///
/// Раньше плашка умела только показывать таймер и честно писала «Стоп — в
/// приложении». Телефон на столе экраном вниз, встреча кончилась — чтобы
/// остановить запись, надо было разблокировать телефон и найти приложение.
/// Всё это время писался звук, который уже никому не нужен: лишние минуты в
/// файле, лишний трафик в iCloud, лишняя работа Mac.
///
/// `LiveActivityIntent` выполняется в процессе САМОГО приложения (система его
/// для этого будит), поэтому останавливать запись можно напрямую — без
/// URL-схем и без пробуждения интерфейса.
@available(iOS 17.0, *)
struct StopRecordingIntent: LiveActivityIntent {
    static var title: LocalizedStringResource = "Stop recording"
    /// Плашка не должна открывать приложение: смысл кнопки в том, чтобы не
    /// трогать телефон вообще.
    static var openAppWhenRun = false

    init() {}

    func perform() async throws -> some IntentResult {
        await RecordingControl.stopFromLiveActivity()
        return .result()
    }
}

/// Мост между интентом (общий таргет) и записью (таргет приложения).
///
/// Intent живёт в Shared, потому что его должен видеть и виджет — он ставит
/// кнопку. Сам `Recorder` виджету недоступен и не должен быть: там нет ни
/// микрофона, ни сессии. Приложение подписывает сюда свой обработчик на
/// старте, расширение просто вызывает.
@MainActor
public enum RecordingControl {
    /// Что делать по нажатию «Стоп». Ставит приложение при запуске.
    public static var onStop: (() -> Void)?

    static func stopFromLiveActivity() {
        onStop?()
    }
}
