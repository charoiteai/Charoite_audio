import CoreAudio
import Foundation

#if os(macOS)

/// Системный звук без BlackHole: Core Audio process tap (macOS 14.4+).
///
/// Раньше пользователь обязан был поставить сторонний драйвер BlackHole и
/// руками переключить выход на «Многовыходное устройство» — два шага, на
/// которых установка и спотыкалась. Тап делает то же самое средствами системы:
/// приложение создаёт его на время встречи, демон читает получившееся
/// устройство как обычный вход, после встречи тап уничтожается.
///
/// Проверено прототипом 05.08.2026 на macOS 26.5: питон-конвейер видит
/// устройство через PortAudio и берёт с него звук. Две грабли, стоившие
/// половины разведки, зашиты в код ниже:
///
/// 1. **Тап на агрегатный выход молчит.** Если выход по умолчанию — Multi-Output
///    Device (а он именно такой у всех, кто ставил BlackHole), тап отдаёт
///    нули без единой ошибки. Тапить нужно физическое устройство.
/// 2. **Приватность тапа и агрегата обязана совпадать.** Публичный агрегат с
///    приватным тапом даёт ноль входных каналов и тишину. Демон — отдельный
///    процесс, поэтому у нас публичны оба.
@available(macOS 14.4, *)
@MainActor
final class SystemAudioTap {
    /// Имя устройства, по которому его ищет `src/audio.py`. Меняя строку здесь,
    /// поменяй и там: это контракт между приложением и демоном.
    static let deviceName = "Charoite System Audio"
    private static let uidPrefix = "ai.charoite.systemaudio."

    private var tapID = AudioObjectID(kAudioObjectUnknown)
    private var aggregateID = AudioObjectID(kAudioObjectUnknown)

    var isActive: Bool { aggregateID != AudioObjectID(kAudioObjectUnknown) }

    /// Поднять тап. Возвращает имя устройства или nil, если система отказала —
    /// вызывающий в этом случае обязан откатиться на BlackHole, а не остаться
    /// без второй стороны разговора.
    @discardableResult
    func start() -> String? {
        guard !isActive else { return Self.deviceName }
        Self.cleanupOrphans()

        let uuid = UUID()
        // Моно, а не стерео: конвейер всё равно сводит канал в моно
        // (Capture открывает поток с channels=1), так что стерео-тап гнал бы
        // вдвое больше данных ради немедленного даунмикса.
        let description = CATapDescription(monoGlobalTapButExcludeProcesses: [])
        description.name = Self.deviceName
        description.uuid = uuid
        description.isPrivate = false      // демон — другой процесс, см. грабля 2
        description.muteBehavior = .unmuted // звук продолжает идти в колонки
        // isExclusive НЕ трогаем: инициализатор ставит true («тапить всё, КРОМЕ»).
        // Сброс в false переворачивает смысл на «тапить ТОЛЬКО перечисленные»,
        // а список пуст — получается тишина без единой ошибки.

        var tap = AudioObjectID(kAudioObjectUnknown)
        guard AudioHardwareCreateProcessTap(description, &tap) == noErr,
              tap != AudioObjectID(kAudioObjectUnknown) else {
            log("тап не создан — остаёмся на BlackHole")
            return nil
        }
        tapID = tap

        guard let outputUID = Self.tappableOutputUID() else {
            stop()
            return nil
        }
        let settings: [String: Any] = [
            kAudioAggregateDeviceNameKey as String: Self.deviceName,
            kAudioAggregateDeviceUIDKey as String: Self.uidPrefix + uuid.uuidString,
            kAudioAggregateDeviceMainSubDeviceKey as String: outputUID,
            kAudioAggregateDeviceIsPrivateKey as String: false,
            kAudioAggregateDeviceIsStackedKey as String: false,
            kAudioAggregateDeviceSubDeviceListKey as String: [
                [kAudioSubDeviceUIDKey as String: outputUID],
            ],
            // Тап должен ожить вместе с устройством, иначе первые секунды
            // встречи уходят в тишину до первого чтения.
            kAudioAggregateDeviceTapAutoStartKey as String: true,
            kAudioAggregateDeviceTapListKey as String: [
                [kAudioSubTapUIDKey as String: uuid.uuidString,
                 kAudioSubTapDriftCompensationKey as String: true],
            ],
        ]
        var aggregate = AudioObjectID(kAudioObjectUnknown)
        guard AudioHardwareCreateAggregateDevice(settings as CFDictionary, &aggregate) == noErr,
              aggregate != AudioObjectID(kAudioObjectUnknown) else {
            stop()
            log("агрегат не создан — остаёмся на BlackHole")
            return nil
        }
        aggregateID = aggregate

        // Ноль входных каналов = тап не подцепился. Молча отдать такое
        // устройство демону нельзя: он запишет тишину и мы узнаем об этом
        // из пустой стенограммы после встречи.
        guard Self.inputChannels(aggregate) > 0 else {
            stop()
            log("устройство без входных каналов — остаёмся на BlackHole")
            return nil
        }
        log("системный звук через тап: «\(Self.deviceName)»")
        return Self.deviceName
    }

    /// Снять тап. Вызывать обязательно: незакрытое устройство остаётся в
    /// системе и висит в списке звуковых устройств до перезагрузки.
    func stop() {
        if aggregateID != AudioObjectID(kAudioObjectUnknown) {
            AudioHardwareDestroyAggregateDevice(aggregateID)
            aggregateID = AudioObjectID(kAudioObjectUnknown)
        }
        if tapID != AudioObjectID(kAudioObjectUnknown) {
            AudioHardwareDestroyProcessTap(tapID)
            tapID = AudioObjectID(kAudioObjectUnknown)
        }
    }

    // MARK: - Устройство вывода

    /// UID устройства, на которое можно повесить тап.
    ///
    /// Выход по умолчанию берём только если он физический: на агрегатном
    /// (Multi-Output) тап отдаёт тишину — см. грабля 1.
    private static func tappableOutputUID() -> String? {
        var device = defaultOutputDevice()
        if device == kAudioObjectUnknown || transportType(device) == kAudioDeviceTransportTypeAggregate {
            guard let physical = firstPhysicalOutput() else { return nil }
            device = physical
        }
        return deviceUID(device)
    }

    private static func defaultOutputDevice() -> AudioDeviceID {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultOutputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        var device = AudioDeviceID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject),
                                   &address, 0, nil, &size, &device)
        return device
    }

    private static func firstPhysicalOutput() -> AudioDeviceID? {
        allDevices().first { device in
            let transport = transportType(device)
            guard transport != kAudioDeviceTransportTypeAggregate,
                  transport != kAudioDeviceTransportTypeVirtual else { return false }
            return channels(device, scope: kAudioObjectPropertyScopeOutput) > 0
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

    private static func transportType(_ device: AudioDeviceID) -> UInt32 {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyTransportType,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        var transport = UInt32(0)
        var size = UInt32(MemoryLayout<UInt32>.size)
        AudioObjectGetPropertyData(device, &address, 0, nil, &size, &transport)
        return transport
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

    static func inputChannels(_ device: AudioDeviceID) -> Int {
        channels(device, scope: kAudioObjectPropertyScopeInput)
    }

    private static func channels(_ device: AudioDeviceID, scope: AudioObjectPropertyScope) -> Int {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyStreamConfiguration,
            mScope: scope,
            mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(device, &address, 0, nil, &size) == noErr,
              size > 0 else { return 0 }
        let raw = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: 16)
        defer { raw.deallocate() }
        guard AudioObjectGetPropertyData(device, &address, 0, nil, &size, raw) == noErr else { return 0 }
        let list = UnsafeMutableAudioBufferListPointer(raw.assumingMemoryBound(to: AudioBufferList.self))
        return list.reduce(0) { $0 + Int($1.mNumberChannels) }
    }

    /// Убрать наши устройства, пережившие падение приложения: иначе они
    /// накапливаются в системе и демон может выбрать мёртвое.
    ///
    /// Зовётся не только перед стартом тапа, но и при запуске и выходе
    /// приложения: 06.08 агрегат, осиротевший после kill приложения,
    /// подвесил CoreAudio всей машины — звук вернул только рестарт
    /// coreaudiod. Уборки в start() мало: тап может быть выключен, а
    /// сирота — оставаться.
    static func cleanupOrphans() {
        for device in allDevices() where deviceUID(device)?.hasPrefix(uidPrefix) == true {
            AudioHardwareDestroyAggregateDevice(device)
        }
    }

    private func log(_ message: String) {
        NSLog("[SystemAudioTap] %@", message)
    }
}

#endif
