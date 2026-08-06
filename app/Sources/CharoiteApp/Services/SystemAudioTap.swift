import CoreAudio
import CoreGraphics
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
        // Без права на запись экрана/системного звука тап физически бесполезен:
        // устройство создаётся, PortAudio его видит, но IO не запускается
        // (EAGAIN) и не отдаёт ни кадра — тишина без единой ошибки. Доказано
        // 06.08: preflight у приложения НЕТ, у терминала ЕСТЬ, и тот же тап
        // из терминала играет. Проверяем ДО создания устройств.
        if !CGPreflightScreenCaptureAccess() {
            // Диалог показывается один раз на подпись. Наша сменилась
            // (ad-hoc → Developer ID), поэтому старый включённый тумблер
            // системой уже не сопоставляется и запрос молча отклоняется —
            // человеку придётся добавить приложение в список руками.
            let granted = CGRequestScreenCaptureAccess()
            logSelfTest("нет права на системный звук; запрос вернул \(granted)")
            guard granted else {
                log("нет разрешения на запись системного звука — остаёмся на BlackHole")
                return nil
            }
        }
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
        selfTest(aggregate)
        log("системный звук через тап: «\(Self.deviceName)»")
        return Self.deviceName
    }

    /// Короткое чтение агрегата СВОИМ IOProc — две задачи разом.
    ///
    /// 1. Диагностика стороны-создателя: стендовые опыты 06.08 показали, что
    ///    тап из терминала отдаёт кадры и одиночному, и парному PortAudio, —
    ///    а демон приложения получал ноль. Осталась одна переменная: личность
    ///    процесса. Самопроверка отвечает, жив ли тап у самого приложения.
    /// 2. TCC: разрешение на запись системного звука выдаётся тому, кто
    ///    читает. Демон — дочерний python, его чтение диалога не показывает
    ///    (ровно та же грабля, что была с микрофоном — см. ensureMicrophone).
    ///    Читаем сами, чтобы диалог пришёл приложению.
    ///
    /// Результат — в ~/Library/Logs/Charoite/tap_selftest.log: unified log
    /// прячет числа NSLog за <private>, а здесь каждая цифра — улика.
    private func selfTest(_ aggregate: AudioObjectID) {
        // Право на захват системного звука живёт в том же TCC-разделе, что и
        // запись экрана. Preflight отвечает про ТЕКУЩИЙ процесс — то есть
        // именно про приложение, а не про терминал разработчика.
        let allowed = CGPreflightScreenCaptureAccess()
        logSelfTest("право на запись экрана/системного звука: \(allowed ? "ЕСТЬ" : "НЕТ")")
        if !allowed {
            // Диалог показывается один раз на подпись; после смены подписи
            // (ad-hoc → Developer ID) запись TCC перестаёт сопоставляться,
            // и старый включённый тумблер уже ничего не значит.
            let granted = CGRequestScreenCaptureAccess()
            logSelfTest("запросил разрешение, ответ системы: \(granted)")
        }
        var procID: AudioDeviceIOProcID?
        var frames = 0
        var peak: Float = 0
        let block: AudioDeviceIOBlock = { _, inData, _, _, _ in
            let list = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inData))
            for buffer in list {
                guard let data = buffer.mData else { continue }
                let count = Int(buffer.mDataByteSize) / MemoryLayout<Float>.size
                let samples = data.assumingMemoryBound(to: Float.self)
                for i in 0..<count { peak = max(peak, abs(samples[i])) }
                frames += count
            }
        }
        let createStatus = AudioDeviceCreateIOProcIDWithBlock(&procID, aggregate, nil, block)
        guard createStatus == noErr, let proc = procID else {
            logSelfTest("IOProc не создан: OSStatus \(createStatus)")
            return
        }
        // Свежесозданный агрегат может быть не готов к IO мгновенно —
        // пробуем трижды, чтобы отличить «прогревается» от «запрещено».
        var startStatus: OSStatus = noErr
        var started = false
        for attempt in 1...3 {
            startStatus = AudioDeviceStart(aggregate, proc)
            if startStatus == noErr { started = true; break }
            logSelfTest("старт IO, попытка \(attempt): OSStatus \(startStatus)")
            Thread.sleep(forTimeInterval: 0.4)
        }
        guard started else {
            AudioDeviceDestroyIOProcID(aggregate, proc)
            logSelfTest("IO не стартовал после 3 попыток: OSStatus \(startStatus)")
            return
        }
        // 0.8 с на главном потоке — осознанно: это старт записи, короткая
        // пауза незаметна, а честный замер дороже мгновенности.
        Thread.sleep(forTimeInterval: 0.8)
        AudioDeviceStop(aggregate, proc)
        AudioDeviceDestroyIOProcID(aggregate, proc)
        logSelfTest("кадров \(frames), пик \(String(format: "%.4f", peak)) → "
                    + (frames == 0 ? "IO МОЛЧИТ" : peak > 0.0005 ? "ЗВУК ИДЁТ" : "кадры-нули"))
    }

    private func logSelfTest(_ message: String) {
        log("самопроверка тапа: \(message)")
        let dir = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/Charoite", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent("tap_selftest.log")
        let stamp = ISO8601DateFormatter().string(from: Date())
        let line = "\(stamp) \(message)\n"
        if let handle = try? FileHandle(forWritingTo: file) {
            handle.seekToEndOfFile()
            handle.write(Data(line.utf8))
            try? handle.close()
        } else {
            try? Data(line.utf8).write(to: file)
        }
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
