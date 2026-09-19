import AVFoundation
import Foundation

/// Ротация файла и делегат рекордера — вынесены из `Recorder.swift` по лимиту
/// длины файла (SwiftLint file_length); хранимые свойства `encodeErrors` и
/// `rotating` остаются в классе, логика — здесь.
extension Recorder {
    /// Потерять полчаса разговора хуже, чем получить встречу двумя кусками.
    /// Склейки на Mac НЕТ: импорт различает записи по имени и размеру, и каждый
    /// кусок становится своей встречей в графе (аудит 13.09, DS I2; склейка
    /// сегментов `iphone_*` — отдельная карточка). Если вход после стопа занят,
    /// старт взводится и поднимется сам — при открытом приложении: в фоне
    /// таймер проб не тикает (GLM M5).
    func rotateFile(reason: StopReason) {
        let kind = currentKind
        rotating = true                   // свой флаг: rotateTask гаснет от истечения бюджета (DS I1 r2)
        beginRotateTask()
        stop(reason: reason)              // причина — параметром, не копией lastResult (Important GLM по №200)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.7) { [weak self] in
            guard let self else { return }
            defer {
                self.rotating = false
                self.endRotateTask()
            }
            guard !self.isRecording else { return }
            self.start(kind: kind)
        }
    }

    nonisolated func audioRecorderEncodeErrorDidOccur(_ recorder: AVAudioRecorder, error: Error?) {
        Task { @MainActor [weak self] in
            // ошибка финализации после «Стоп» или от старого рекордера после ротации —
            // не повод трогать запись (GLM I1 r1, GLM I1 / DS M1 r2 по #565)
            guard let self, self.isRecording, recorder === self.recorder else { return }
            self.encodeErrors += 1
            if Self.actionAfterEncodeError(consecutive: self.encodeErrors) == .rotate {
                self.lastResult = L.t("Сбой записи (\(error?.localizedDescription ?? "кодек")) — закрываю файл и продолжаю встречу новым",
                                      "Recording error (\(error?.localizedDescription ?? "codec")) — closing the file and continuing in a new one",
                                      "录音错误（\(error?.localizedDescription ?? "编解码器")）— 关闭文件并以新文件继续")
                self.rotateFile(reason: .encodeError)
            } else {
                self.lastResult = L.t("Сбой записи: \(error?.localizedDescription ?? "кодек") — \(Self.maxEncodeErrors) раза подряд, запись остановлена",
                                      "Recording error: \(error?.localizedDescription ?? "codec") — \(Self.maxEncodeErrors) times in a row, recording stopped",
                                      "录音错误：\(error?.localizedDescription ?? "编解码器") — 连续 \(Self.maxEncodeErrors) 次，录音已停止")
                self.stop(reason: .encodeError)
            }
        }
    }

    nonisolated func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder,
                                                     successfully flag: Bool) {
        guard !flag else { return }
        let url = recorder.url
        Task { @MainActor [weak self] in
            // исход финализации приходит асинхронно и после ротации относится к
            // СТАРОМУ файлу: пометка — в его манифест, а не в статус здоровой
            // записи (Critical DS входного круга по №200)
            StopRecord.markUnfinalized(audio: url)
            guard let self, self.recorder == nil || self.recorder === recorder else { return }
            self.lastResult = L.t("Запись завершилась с ошибкой — файл может быть неполным",
                                  "Recording finished with an error — file may be incomplete",
                                  "录音异常结束 — 文件可能不完整")
        }
    }
}
