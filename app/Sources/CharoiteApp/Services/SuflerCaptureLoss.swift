import CoreGraphics
import Foundation

#if os(macOS)

/// Потеря захвата ScreenCaptureKit посреди встречи (карточка №35).
///
/// Отдельным файлом по той же причине, что и `SuflerShutdown`: сервис
/// перерос лимит длины, а это цельный сценарий — поток умер и не
/// пересоздался, человек узнаёт сразу, запись перезапускается дорогой
/// сторожа (не больше двух раз за встречу), третья потеря и сознательный
/// «Стоп» в системном индикаторе закрывают встречу дорогой кнопки с
/// сохранением записи.
@MainActor
extension SuflerService {

    /// Натив не поднялся на СТАРТЕ и встреча уходит на BlackHole — что сказать.
    ///
    /// nil — молчать: права нет, выдача — шаг установки, о нём уже говорит
    /// готовность (SETUP). Говорим в двух случаях: право есть, а захват не
    /// поднялся (аномалия №140 — встреча 15:43 ушла на фолбэк без единого
    /// слова человеку) и право выдано только что (нужен перезапуск — P0-10).
    nonisolated static func captureFallbackMessage(
        _ access: SetupReadinessPolicy.ScreenCaptureAccess
    ) -> String? {
        switch access {
        case .granted:
            return L.t("Системный звук не поднялся — встреча пишется через BlackHole",
                       "System audio capture did not start — the meeting is recorded via BlackHole",
                       "系统音频捕获未启动——会议将通过 BlackHole 录制")
        case .grantedNeedsRestart:
            return L.t("Право на системный звук заработает после перезапуска приложения — пока пишем через BlackHole",
                       "The system-audio permission takes effect after the app restarts — recording via BlackHole for now",
                       "系统音频权限将在应用重启后生效——目前通过 BlackHole 录制")
        case .denied:
            return nil
        }
    }

    /// №140: ScreenCaptureKit не поднялся на старте записи, а приложение
    /// молча уходило на BlackHole — человек узнавал из логов (или никогда).
    func announceCaptureFallback() {
        let access = SetupReadinessPolicy.screenCaptureAccess(
            preflight: CGPreflightScreenCaptureAccess(),
            grantedInThisSession: SystemAudioCapture.accessGrantedInThisSession)
        guard let message = Self.captureFallbackMessage(access) else { return }
        status = message
        MeetingNotificationService.shared.presentCaptureFallback(message)
    }

    /// Поток ScreenCaptureKit умер и не пересоздался: человек должен узнать
    /// сразу, а не из пустой стенограммы — на macOS 15 с ним уходит и
    /// микрофон.
    func captureLost(reason: String, userStopped: Bool) {
        guard isRunning, let p = process, p.isRunning else { return }
        MeetingNotificationService.shared.presentCaptureLost(reason)
        if userStopped {
            // Человек сам дважды остановил захват в системном
            // индикаторе — это не сбой: бюджет потерь не тратим, а
            // встречу закрываем с сохранением. Оставить её «идущей»
            // без звука нельзя: через 100 с сторож перезапустил бы
            // запись и поднял захват против воли человека (круг-4
            // по PR #383, Codex).
            closeMeetingAfterCaptureLoss(
                L.t("Захват звука остановлен вами (\(reason)) — запись завершена; начните заново, если это не нарочно",
                    "You stopped the audio capture (\(reason)) — the recording is finished; start again if that was not intentional",
                    "音频捕获已被您停止（\(reason)）——录音已结束；若非有意，请重新开始"))
            return
        }
        captureLossCount += 1
        if captureLossCount > Self.captureLossLimit {
            // Третья потеря за встречу: перезапуски не лечат. Встречу
            // закрываем штатно и с сохранением — иначе сторож через
            // 100 с молча перезапустил бы её ещё раз, а человек читал
            // бы «перезапуски не помогают» поверх нового старта
            // (круг-3 по PR #383, DS + Codex).
            closeMeetingAfterCaptureLoss(
                L.t("Захват звука потерян снова (\(reason)) — перезапуски не помогают; запись остановлена, начните заново",
                    "Audio capture lost again (\(reason)) — restarts do not help; the recording is stopped, start it again",
                    "音频捕获再次丢失（\(reason)）——重启无效；录音已停止，请重新开始"))
            return
        }
        // Не ждём 100-секундного сторожа: та же дорога, что у него, —
        // демон гасится, daemonDied перезапускает встречу с новым
        // захватом (и откатом на BlackHole, если ScreenCaptureKit
        // так и не вернулся). Демон при этом ЖИВОЙ — ему дают те же
        // 12 с на штатное завершение, что и кнопке Стоп.
        captureLossReason = reason
        fail(L.t("Захват звука встречи потерян (\(reason)) — перезапускаю запись",
                      "Meeting audio capture lost (\(reason)) — restarting the recording",
                      "会议音频捕获已丢失（\(reason)）——正在重启录音"))
        p.terminate()
        DispatchQueue.global().asyncAfter(deadline: .now() + 12.0) {
            if p.isRunning { kill(p.processIdentifier, SIGKILL) }
        }
    }

    func captureRecovered() {
        guard isRunning else { return }
        status = L.t("Захват звука восстановлен после сбоя",
                          "Audio capture recovered after a failure",
                          "音频捕获在故障后已恢复")
    }

    /// Встреча закрывается из-за захвата (третья потеря или сознательный
    /// стоп человека): демон жив, поэтому идём дорогой кнопки «Стоп» —
    /// «stop» в stdin, страховочные таймеры, закрытие захвата после смерти
    /// читателя. Запись и граф сохраняются; сторож дальше не перезапускает.
    /// Причина показывается сразу и восстанавливается после финальных
    /// статусов демона.
    func closeMeetingAfterCaptureLoss(_ text: String) {
        fail(text)
        preservedFailure = text
        captureLossExhausted = true
        guard let token = gateBeginStop() else { return }
        cleanupDisposition = .preserveFailure
        beginDaemonStop(token: token)
    }

}
#endif
