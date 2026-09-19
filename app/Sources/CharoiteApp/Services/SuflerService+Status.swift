import Foundation

/// Статусные флаги и подсказки — вынесены из `SuflerService.swift`: файл упёрся в
/// потолок SwiftLint (1000 строк), а эти методы не трогают приватного состояния.
extension SuflerService {
    func fail(_ text: String) {
        status = text
        statusIsError = true
        statusErrorFromDaemon = false
    }

    /// Снять оба флага ошибки: «захват восстановлен» с одним снятым флагом прятал
    /// устойчивый баннер отказа диска (DS I4 по #564).
    func clearErrorFlags() {
        statusIsError = false
        statusErrorFromDaemon = false
    }

    /// Баннеры автостопа и потери захвата «показываются всегда», но при отказе в
    /// праве система их молча глотает, и код об этом не узнавал (аудит 13.09,
    /// GLM I1). Говорим об этом в окне один раз за встречу своим слоем: раньше
    /// строка писалась «если слот пуст» и терялась при любом более важном
    /// предупреждении; теперь её место решает приоритет слоёв, а «один раз за
    /// встречу» держит флаг `notificationsDeniedShown` (№310).
    func noteNotificationsDenied() {
        guard !notificationsDeniedShown else { return }
        notificationsDeniedShown = true
        setSticky(StickyTopic.notifications,
                  L.t("Уведомления выключены: об автостопе и потере захвата скажет только это окно",
                      "Notifications are off: autostop and capture loss are reported only in this window",
                      "通知已关闭：自动停止和捕获丢失只会在此窗口提示"))
    }

    /// Почему микрофона нет в потоке ScreenCaptureKit — липкая строка на всю встречу
    /// (DS I1/I2, DS M1 и критика DS r2 по #564).
    static func micFallbackText(_ kind: SystemAudioCapture.MicFallback) -> String {
        switch kind {
        case .denied:
            return L.t("Права на микрофон нет — голос владельца не запишется",
                       "No microphone permission — your own voice will not be recorded",
                       "没有麦克风权限——您的声音不会被录制")
        case .noDevice:
            return L.t("Устройства ввода нет — пишется только системный звук",
                       "No input device — only system audio is recorded",
                       "没有输入设备——仅录制系统音频")
        case .silent, .none:
            return L.t("Микрофон не попал в поток системного звука — пишется отдельно",
                       "The microphone did not join the system-audio stream — recorded separately",
                       "麦克风未进入系统音频流——将单独录制")
        }
    }
}
