import AVFoundation
import Foundation

#if os(macOS)
import AppKit

/// Остановка записи: кнопка «Стоп», страховочные таймеры, закрытие захвата.
///
/// Вынесено из `SuflerService` отдельным файлом по двум причинам. Первая
/// формальная: сервис перерос лимит длины файла. Вторая по делу — это
/// цельная подсистема со своим состоянием (`ShutdownMachine`), и держать её
/// рядом со стартом, подсказками и разбором stdout значит каждый раз читать
/// весь сервис, чтобы понять один сценарий. Решения о переходах живут в
/// чистом типе `ShutdownMachine`, здесь — только исполнение его действий.
@MainActor
extension SuflerService {

    func stop() {
        // Stop во время уже идущей очистки отменяет запланированный
        // auto-restart, даже если нового state transition не требуется.
        userStopped = true
        if lifecycle == .stopping {
            cleanupDisposition = .stopped
            // Стоп по ЗАСТРЯВШЕМУ демону — просьба добить его ещё раз.
            // Раньше метод здесь просто выходил, и человек жал кнопку впустую
            // (аудит 14.08: «.blocked без выходных дуг»).
            applyShutdown(.stopRequested(daemonAlive: process?.isRunning == true),
                          token: stopToken)
            return
        }
        guard let token = gateBeginStop() else { return }
        cleanupDisposition = .stopped
        beginDaemonStop(token: token)
        status = L.t("Останавливаю…", "Stopping…", "停止中…")
    }

    /// Остановка встречи с ЖИВЫМ демоном: «stop» в stdin, страховочные
    /// terminate/SIGKILL, запасной таймер, закрытие захвата после смерти
    /// читателя. Общий путь кнопки «Стоп» и закрытия встречи по потере
    /// захвата: прямой `beginCaptureShutdown` при живом демоне закрывал
    /// только ScreenCaptureKit, а демон и lifecycle `.stopping` оставались
    /// навсегда (круг-4 по PR #383, Codex). Диспозицию и статус ставит
    /// вызывающий: кнопка — `.stopped`, потеря захвата — `.preserveFailure`.
    func beginDaemonStop(token: UUID) {
        let wasRecording = lifecycle == .recording
        // Фазу заводим ЗДЕСЬ, через машину, а не при первой проверке
        // процесса. Иначе она остаётся `.idle`, и тогда: повторный Стоп
        // попадает в переход «начать остановку» и гасит страховочный
        // таймер, а сам таймер на `.idle` вырождается в «ничего не делать»
        // — то есть при демоне, пережившем SIGKILL, выхода из остановки не
        // остаётся вовсе (ревью 19.08, круги 2 и 3).
        shutdownPhase = .idle
        applyShutdown(.stopRequested(daemonAlive: process?.isRunning == true),
                      token: token)
        publishLifecycle()

        if wasRecording { MeetingProcessingService.shared.expectResult() }
        watchdog?.invalidate()
        watchdog = nil
        if wasRecording { send("stop") }
        captureStartTask?.cancel()

        // Демону нужно успеть: запустить graph_updater и закрыть аудио-стримы.
        // 1.5с не хватало на длинной встрече — обновление графа терялось.
        let p = process  // сильный захват: добить именно ЭТОТ демон, не преемника
        if let p, p.isRunning {
            DispatchQueue.global().asyncAfter(deadline: .now() + 8.0) {
                if p.isRunning { p.terminate() }
            }
            // Зависший в finally демон держит daemon.lock. Добиваем именно
            // захваченный Process; lifecycle до его смерти остаётся stopping.
            DispatchQueue.global().asyncAfter(deadline: .now() + 12.0) {
                if p.isRunning { kill(p.processIdentifier, SIGKILL) }
            }
            scheduleStopFallback(token: token)
        } else {
            beginCaptureShutdown(token: token)
        }
        endSleepGuard()
        stopClock()
    }

    func beginFailedStartCleanup(token: UUID) {
        guard gateOwns(token, in: .starting),
              let stopToken = gateBeginStop()
        else { return }
        cleanupDisposition = .preserveFailure
        publishLifecycle()
        beginCaptureShutdown(token: stopToken)
    }

    /// Последняя страховка: если terminationHandler почему-то не пришёл,
    /// через 13 секунд capture всё равно закроется. В idle переходим только
    /// после await stop(), поэтому новая встреча не перекрывает старую.
    private func scheduleStopFallback(token: UUID) {
        stopFallbackTask?.cancel()
        stopFallbackTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(nanoseconds: 13_000_000_000)
            } catch {
                return
            }
            guard let self, self.gateOwns(token, in: .stopping) else { return }
            // Через машину, а не мимо неё: иначе событие «запасной таймер»
            // остаётся объявленным и оттестированным, но недостижимым в
            // проде (ревью 19.08).
            self.applyShutdown(.killTimeout, token: token)
        }
    }

    /// Закрывает сначала незавершённый startCapture, затем сам capture.
    /// Один token может войти сюда и из terminationHandler, и из fallback —
    /// `captureShutdownToken` делает операцию идемпотентной.
    /// Единственный вход в подмашину остановки: событие внутрь, действие
    /// наружу — и оно СРАЗУ исполняется.
    ///
    /// Отдельным методом, потому что дважды на ревью всплыл один и тот же
    /// класс дефекта: событие объявлено в машине, покрыто зелёным тестом —
    /// и не подаётся из сервиса; либо действие возвращается и молча
    /// выбрасывается. Тест при этом закрепляет поведение, которого в
    /// системе нет, а это хуже отсутствия теста. Пока подача события и
    /// исполнение действия были разнесены по коду, ловушка воспроизводилась
    /// снова и снова (ревью 19.08, круги 1 и 2).
    /// `closingCapture` — признак «нас позвали ИЗНУТРИ закрытия захвата».
    /// Тогда действия, которые сами ведут в это закрытие, не выполняются
    /// повторно, а возвращаются наружу: иначе получилась бы рекурсия. Токен
    /// при этом передаётся обязательно — на нём держится планирование
    /// следующего опроса, и с `nil` цикл ожидания обрывался бы на первом
    /// шаге, оставляя встречу незакрытой.
    @discardableResult
    func applyShutdown(_ event: ShutdownEvent, token: UUID?,
                               closingCapture: Bool = false) -> ShutdownAction {
        let (phase, action) = ShutdownMachine.next(shutdownPhase, on: event)
        shutdownPhase = phase
        switch action {
        case .nothing:
            break
        case .closeCapture, .finish:
            guard !closingCapture else { break }   // уже внутри — вернём наружу
            if let token { beginCaptureShutdown(token: token) }
        case .pollAgain(let delay):
            captureShutdownToken = nil
            if let token { scheduleShutdownPoll(token: token, after: delay) }
        case .reportStuck:
            captureStartTask = nil
            captureShutdownToken = nil
            fail(L.t(
                "Процесс записи не завершился — жду его, можно нажать «Стоп» ещё раз",
                "The recording process did not stop — still waiting; press Stop again to force it",
                "录音进程未能停止——仍在等待；可再次点击「停止」强制结束"
            ))
            if let token { scheduleShutdownPoll(token: token, after: ShutdownMachine.slowPoll) }
        case .forceKill:
            guard let p = process, p.isRunning, let token else { break }
            status = L.t("Добиваю процесс записи…", "Force-stopping the recorder…",
                         "正在强制停止录音进程…")
            kill(p.processIdentifier, SIGKILL)
            scheduleShutdownPoll(token: token, after: ShutdownMachine.fastPoll)
        }
        return action
    }

    /// Следующая проверка процесса. Интервал приходит из подмашины: частый
    /// пока ждём, редкий — когда демон уже признан застрявшим.
    private func scheduleShutdownPoll(token: UUID, after delay: TimeInterval) {
        stopFallbackTask?.cancel()
        stopFallbackTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard let self, self.gateOwns(token, in: .stopping) else { return }
            self.beginCaptureShutdown(token: token)
        }
    }

    func beginCaptureShutdown(token: UUID) {
        guard gateOwns(token, in: .stopping),
              captureShutdownToken != token
        else { return }
        captureShutdownToken = token
        stopFallbackTask?.cancel()
        stopFallbackTask = nil

        let startTask = captureStartTask
        startTask?.cancel()
        let capture = systemAudioCapture
        systemAudioCapture = nil
        let tap = systemAudioTap
        systemAudioTap = nil

        Task { @MainActor [weak self] in
            _ = await startTask?.value
            if #available(macOS 14.4, *) { (tap as? SystemAudioTap)?.stop() }
            if #available(macOS 13.0, *) {
                await (capture as? SystemAudioCapture)?.stop()
            }
            guard let self else { return }
            // SIGKILL запланирован на 12-ю секунду, но termination notification
            // может прийти чуть позже. Не открываем idle, пока старый daemon
            // действительно жив: иначе следующий Start снова получит два
            // процесса, несмотря на исправленный capture.
            // Опрос идёт через тот же единственный вход. `.finish` — это
            // «закрывать встречу», и обрабатывается ниже по коду; всё
            // остальное (ещё подождать, объявить застревание) машина уже
            // исполнила внутри applyShutdown — с ТЕМ ЖЕ токеном, на котором
            // держится планирование следующего опроса.
            //
            // `closingCapture: true` — мы уже внутри закрытия захвата,
            // поэтому действия, ведущие обратно сюда, машина не выполняет, а
            // возвращает наружу.
            let action = self.applyShutdown(
                .pollTick(daemonAlive: self.process?.isRunning == true),
                token: token, closingCapture: true)
            guard action == .finish else { return }

            guard self.gateFinishStop(
                token,
                daemonAlive: self.process?.isRunning == true
            ) else { return }
            self.captureStartTask = nil
            self.captureShutdownToken = nil
            self.shutdownPhase = .done
            self.process = nil
            self.publishLifecycle()

            if let final = Self.finalStatus(disposition: self.cleanupDisposition,
                                            preservedFailure: self.preservedFailure,
                                            autostopReason: self.autostopReason) {
                self.status = final.text
                self.statusIsError = final.isError
            }
            self.preservedFailure = nil
            if case .restart = self.cleanupDisposition {
                DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
                    guard let self, !self.userStopped, self.lifecycle == .idle else { return }
                    self.start(preserveUI: true)
                }
            }
        }
    }

    /// Статус после очистки. Кнопка — «Остановлен» или причина автостопа:
    /// раньше здесь безусловно писалось «Остановлен», и человек, вернувшийся
    /// к ноутбуку, не отличал автостоп от своего Стопа (ревью 18.08 ×2).
    /// Закрытие по захвату — сохранённая причина: по пути остановки демон
    /// шлёт свои статусы («Финальная стенограмма…»), и `consume` затирает
    /// ими текст потери; без восстановления человек видел бы обычный финал
    /// вместо «захват потерян» (круг-5 по PR #383, Codex). Перезапуск —
    /// статус не трогаем.
    static func finalStatus(disposition: CleanupDisposition, preservedFailure: String?,
                            autostopReason: String?) -> (text: String, isError: Bool)? {
        switch disposition {
        case .stopped:
            return (stoppedStatus(autostopReason: autostopReason), false)
        case .preserveFailure:
            return preservedFailure.map { ($0, true) }
        case .restart:
            return nil
        }
    }

}
#endif
