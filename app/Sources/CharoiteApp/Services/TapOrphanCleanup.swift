import CoreAudio
import Foundation

#if os(macOS)

/// Уборка за Core Audio process tap — всё, что осталось от второго подхода
/// к системному звуку.
///
/// Сам тап (06–07.08.2026) звук приносил, но цикл создания и уничтожения
/// тап-агрегата клинил CoreAudio на macOS 26.5: после встречи динамики
/// машины умолкали до перезапуска `coreaudiod`. Путь выключили 07.08 (#256),
/// системный звук с тех пор берёт ScreenCaptureKit (`SystemAudioCapture`),
/// а 02.09 код тапа снят целиком — резерв, который лишает машину звука,
/// резервом не является. Если понадобится вернуть: полный файл с граблями
/// в комментариях (публичный агрегат × приватный тап, тап на Multi-Output
/// молчит, `isExclusive`, EAGAIN на первом старте, `Int16(NaN)`) —
/// `git show 46792c1:app/Sources/CharoiteApp/Services/SystemAudioTap.swift`.
///
/// Остаётся одно: агрегаты с нашим префиксом, пережившие те версии или
/// падение приложения, подвешивают CoreAudio всей машины (06.08 такой
/// сирота лечился только рестартом `coreaudiod`). Поэтому уборка зовётся
/// при запуске и при выходе приложения. Срок: после того как когорта
/// обновления пройдёт (ориентир — 0.70), enum можно снять вместе с
/// ретеншном `tap_stream.*` в `audio.py`.
@available(macOS 14.4, *)
@MainActor
enum TapOrphanCleanup {
    /// Префикс UID агрегатов, которые создавали версии с тапом.
    private static let uidPrefix = "ai.charoite.systemaudio."

    /// Уничтожить наши агрегаты, пережившие те версии или падение приложения.
    static func cleanupOrphans() {
        for device in allDevices() where deviceUID(device)?.hasPrefix(uidPrefix) == true {
            AudioHardwareDestroyAggregateDevice(device)
        }
    }

    private static func allDevices() -> [AudioDeviceID] {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDevices,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject),
                                             &address, 0, nil, &size) == noErr else { return [] }
        var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject),
                                         &address, 0, nil, &size, &ids) == noErr else { return [] }
        return ids
    }

    private static func deviceUID(_ device: AudioDeviceID) -> String? {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyDeviceUID,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        var uid: CFString = "" as CFString
        var size = UInt32(MemoryLayout<CFString>.size)
        let status = withUnsafeMutablePointer(to: &uid) {
            AudioObjectGetPropertyData(device, &address, 0, nil, &size, $0)
        }
        return status == noErr ? (uid as String) : nil
    }
}

#endif
