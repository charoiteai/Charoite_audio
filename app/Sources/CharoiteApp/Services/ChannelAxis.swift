import Foundation

/// Ось канала записи: где в файле должен лечь очередной буфер по его времени.
///
/// Приёмник ScreenCaptureKit писал файл склейкой дошедшего, и каждый
/// выброшенный буфер навсегда сдвигал канал назад. Замер 15.10 (встреча на
/// 31 мин): микрофон ушёл от системного звука ступенями до −5,2 с, без наклона
/// внутри ступени — это выпадения ~1 с, не уход часов (№655). Здесь только
/// арифметика: время буфера на часах хоста против записанных кадров. Писать
/// файлы, считать и логировать — дело `Sink`.
///
/// Дыру больше порога ось закрывает нулями — только перед настоящим буфером:
/// канал, который перестал отдавать звук, файл не растит, и сторож демона видит
/// его тишину, как раньше. Дыра больше потолка — разрыв: нулей нет, отсчёт
/// заново от этого буфера (так же, как жил файл до №655).
struct ChannelAxis {
    /// Меньше — дрожание времени буферов (буфер SCK 10–20 мс), не дыра.
    static let gapThreshold: Double = 0.1
    /// Потолок заливки — половина живого буфера демона (`BUF_CAP_S = 60` в
    /// `audio.py`): больше нулей за раз вытеснили бы из него речь до дыры и
    /// дали бы ложный крик о потере (вход 3 №655, Sonnet C1).
    static let fillCeiling: Double = 30

    /// Частота файла — частота первого буфера канала.
    let rate: Int
    /// Кадров в файле, с нулями.
    private(set) var fileFrames = 0
    /// Ось выключена до конца сессии: пришёл буфер другой частоты. Файл
    /// пишется как до №655 — пересчитать дыру в кадры чужой частоты нельзя.
    private(set) var disabled = false
    private var anchor: (host: Double, frames: Int)?

    // Итог для журнала.
    private(set) var gaps = 0
    private(set) var zeroFrames = 0
    private(set) var breaks = 0
    /// Файл впереди оси больше порога — отсчёт заново от буфера (вход в файл
    /// нельзя отмотать назад).
    private(set) var rebases = 0
    /// Самое большое расхождение, оставленное как есть (дрожание под порогом), с.
    private(set) var worstDrift: Double = 0
    /// Больше — мусор во времени буфера, а не дыра: `Int(...)` от такого упал бы.
    static let saneSpan: Double = 1e6

    init(rate: Int) { self.rate = max(1, rate) }

    enum Plan: Equatable {
        case write                    // буфер встаёт как есть
        case fill(zeros: Int)         // сначала столько нулей
        case broke(gapSeconds: Double)  // разрыв: отсчёт заново
    }

    /// Что сделать перед буфером со временем `host` (секунды часов хоста);
    /// `epoch` — время первого буфера сессии на тех же часах.
    mutating func plan(host: Double, epoch: Double) -> Plan {
        guard !disabled else { return .write }
        let a: (host: Double, frames: Int)
        if let anchor {
            a = anchor
        } else {
            // Первый буфер с временем: файл пуст — от эпохи сессии (поздний
            // старт канала получит ведущие нули), иначе — от себя.
            a = fileFrames == 0 ? (epoch, 0) : (host, fileFrames)
            anchor = a
        }
        guard abs(host - a.host) < Self.saneSpan else {
            anchor = (host, fileFrames)
            breaks += 1
            return .broke(gapSeconds: host - a.host)
        }
        let want = a.frames + Int(((host - a.host) * Double(rate)).rounded())
        let gap = want - fileFrames
        let gapSeconds = Double(gap) / Double(rate)
        if gapSeconds < -Self.gapThreshold {
            // Буфер раньше своего места в файле (поздний канал на старте, чужие
            // часы): записанное не отмотать, а резать звук — потерять его
            // (выход 2 №655). Отсчёт заново от этого буфера, со счётчиком.
            anchor = (host, fileFrames)
            rebases += 1
            return .write
        }
        guard gapSeconds > Self.gapThreshold else {
            worstDrift = max(worstDrift, abs(gapSeconds))
            return .write
        }
        guard gapSeconds <= Self.fillCeiling else {
            anchor = (host, fileFrames)
            breaks += 1
            return .broke(gapSeconds: gapSeconds)
        }
        return .fill(zeros: gap)
    }

    /// Нули легли в файл.
    mutating func didFill(_ zeros: Int) {
        fileFrames += zeros
        zeroFrames += zeros
        gaps += 1
    }

    /// Буфер лёг в файл. Неудачная запись откатывается целиком
    /// (`FileHandle.appendAll`), поэтому счёт кадров остаётся равен файлу, а
    /// якорь по времени — верным: место несостоявшегося буфера зальёт следующая дыра.
    mutating func didWrite(_ frames: Int) { fileFrames += frames }

    /// Запись не удалась: счёт кадров — по позиции файла, а не по памяти оси
    /// (откат мог не пройти). Позиции нет — ось выключается.
    mutating func resync(fileFrames frames: Int?) {
        guard let frames else { return disable() }
        fileFrames = frames
    }

    /// Пришёл буфер другой частоты.
    mutating func disable() { disabled = true }
}

extension FileHandle {
    /// Дописать всё или ничего: при ошибке файл обрезается к смещению до
    /// записи. Иначе часть нулей или буфера легла бы в файл мимо счёта оси, и
    /// канал уехал бы вперёд до конца встречи (выход 1 №655, DS C1, Haiku C2).
    func appendAll(_ body: (FileHandle) throws -> Void) -> Bool {
        let at = try? offsetInFile
        do {
            try body(self)
            return true
        } catch {
            if let at {
                try? truncate(atOffset: at)
                try? seek(toOffset: at)
            }
            return false
        }
    }
}
