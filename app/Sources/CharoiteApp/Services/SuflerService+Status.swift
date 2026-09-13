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
    /// GLM I1). Говорим об этом в окне один раз за встречу — липко, пока не придёт
    /// предупреждение важнее (липкий слот чистится каждым стартом).
    func noteNotificationsDenied() {
        guard !notificationsDeniedShown else { return }
        notificationsDeniedShown = true
        guard stickyStatus == nil else { return }
        stickyStatus = L.t("Уведомления выключены: об автостопе и потере захвата скажет только это окно",
                           "Notifications are off: autostop and capture loss are reported only in this window",
                           "通知已关闭：自动停止和捕获丢失只会在此窗口提示")
    }
}
