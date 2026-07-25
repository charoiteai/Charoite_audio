import AppKit
import Carbon.HIToolbox
import SwiftUI

#if os(macOS)

/// Локальная диктовка: ⌥⌘D — старт/стоп записи, GigaAM распознаёт на
/// устройстве, текст вставляется в активное поле (⌘V с восстановлением
/// буфера). Ничего не покидает мак — наш ответ облачному Wispr Flow.
@MainActor
final class DictationService: ObservableObject {
    static let shared = DictationService()

    @Published var isRecording = false
    @Published var status = ""

    private var proc: Process?
    private var stdinPipe: Pipe?
    private var hotKeyRef: EventHotKeyRef?
    private var noteHotKeyRef: EventHotKeyRef?
    /// Задан — распознанный текст уходит сюда (кнопка-микрофон в чате),
    /// иначе — системная вставка в активное поле (глобальный ⌥⌘D)
    private var onResult: ((String) -> Void)?
    /// Режим голосовой заметки: dictate_note.py сам обрабатывает (qwen),
    /// и сохраняет в граф — Swift только показывает итог
    private var noteMode = false
    private var errTail = ""  // хвост stderr питона — для внятной ошибки
    private var autoStop: Task<Void, Never>?  // предохранитель забытой записи

    private var suflerRoot: URL { AppSettings.charoiteRoot }

    private init() {
        registerHotKey()
    }

    // MARK: - Глобальный хоткей ⌥⌘D

    private func registerHotKey() {
        var eventType = EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                                      eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, event, _ -> OSStatus in
            var hkID = EventHotKeyID()
            GetEventParameter(event, EventParamName(kEventParamDirectObject),
                              EventParamType(typeEventHotKeyID), nil,
                              MemoryLayout<EventHotKeyID>.size, nil, &hkID)
            let id = hkID.id
            Task { @MainActor in
                id == 2 ? DictationService.shared.toggleNote()
                        : DictationService.shared.toggle()
            }
            return noErr
        }, 1, &eventType, nil, nil)
        let hotKeyID = EventHotKeyID(signature: OSType(0x4348_5244), id: 1) // 'CHRD'
        RegisterEventHotKey(UInt32(kVK_ANSI_D), UInt32(cmdKey | optionKey),
                            hotKeyID, GetApplicationEventTarget(), 0, &hotKeyRef)
        // ⌥⌘N — голосовая заметка (обработка + сохранение в граф)
        let noteID = EventHotKeyID(signature: OSType(0x4348_5244), id: 2)
        RegisterEventHotKey(UInt32(kVK_ANSI_N), UInt32(cmdKey | optionKey),
                            noteID, GetApplicationEventTarget(), 0, &noteHotKeyRef)
    }

    func toggle() {
        isRecording ? stop() : start()
    }

    /// Диктовка для чата: старт с колбэком; повторный вызов останавливает
    func toggleInto(_ handler: @escaping (String) -> Void) {
        if isRecording {
            stop()
        } else {
            start(onResult: handler)
        }
    }

    /// Голосовая заметка: наговорил мысль — модель причешет, сохранит в граф
    /// (Заметки/) и запомнит в Чароите. ⌥⌘N или кнопка в меню-баре.
    func toggleNote() {
        if isRecording {
            stop()
        } else {
            start(script: "src/dictate_note.py", note: true)
        }
    }

    // MARK: - Запись (python: sounddevice + GigaAM, всё локально)

    private func start(script: String = "src/dictate.py", note: Bool = false,
                       onResult: ((String) -> Void)? = nil) {
        // proc ещё жив (стоп идёт, распознавание не финишировало) — выходим, НЕ
        // трогая noteMode/onResult in-flight записи. Иначе повторный вызов
        // залипал: чат-диктовка уходила в ветку заметки → «через чат ноль реакции»
        guard proc == nil else { return }
        self.noteMode = note
        self.onResult = onResult
        let p = Process()
        p.executableURL = suflerRoot.appendingPathComponent(".venv/bin/python")
        p.arguments = [script]
        p.currentDirectoryURL = suflerRoot
        let inPipe = Pipe(), outPipe = Pipe(), errPipe = Pipe()
        p.standardInput = inPipe
        p.standardOutput = outPipe
        p.standardError = errPipe
        errTail = ""
        // stderr обязан читаться: python (torch/NeMo) льёт туда предупреждения,
        // при >64КБ он блокировался на записи и не доходил до микрофона.
        // Заодно ловим сигнал REC — момент, когда запись реально пошла
        // (до него 2-5с греется модель, и начало фразы съедалось молча).
        errPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { handle.readabilityHandler = nil; return }
            guard let chunk = String(data: data, encoding: .utf8) else { return }
            Task { @MainActor in
                guard let self else { return }
                self.errTail = String((self.errTail + chunk).suffix(500))
                if chunk.contains("REC"), self.isRecording {
                    self.status = "🎙 запись пошла — говори (⌥⌘D — стоп)"
                }
            }
        }
        p.terminationHandler = { [weak self] proc in
            let data = outPipe.fileHandleForReading.readDataToEndOfFile()
            errPipe.fileHandleForReading.readabilityHandler = nil
            let text = String(data: data, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            Task { @MainActor [weak self] in self?.finished(text: text, exit: proc.terminationStatus) }
        }
        do {
            try p.run()
            proc = p
            stdinPipe = inPipe
            isRecording = true
            status = noteMode ? "🎙 заметка… говори (⌥⌘N — стоп)" : "🎙 диктовка… (⌥⌘D — стоп)"
            NSSound(named: "Pop")?.play()
            // глобальный хоткей легко забыть: не даём писать вечно — часовой
            // wav всё равно не распознается, а микрофон «висит» открытым
            autoStop = Task { [weak self] in
                try? await Task.sleep(for: .seconds(600))
                guard let self, self.isRecording, !Task.isCancelled else { return }
                self.stop()
                self.status = "диктовка остановлена сама через 10 минут — распознаю…"
            }
        } catch {
            status = "диктовка не запустилась: \(error.localizedDescription)"
            // залипший колбэк уводил бы следующую ГЛОБАЛЬНУЮ диктовку в невидимый биндинг
            // (self. — параметр onResult затеняет свойство)
            self.onResult = nil
            self.noteMode = false
        }
    }

    private func stop() {
        autoStop?.cancel()
        autoStop = nil
        status = "распознаю…"
        isRecording = false
        try? stdinPipe?.fileHandleForWriting.close()  // EOF = стоп записи
        // распознавание короткое; зависший процесс добьём
        let p = proc
        DispatchQueue.global().asyncAfter(deadline: .now() + 25) {
            if let p, p.isRunning { p.terminate() }
        }
    }

    private func finished(text: String, exit: Int32) {
        proc = nil
        stdinPipe = nil
        isRecording = false
        let handler = onResult
        onResult = nil
        let wasNote = noteMode
        noteMode = false
        guard !text.isEmpty else {
            status = exit == 0 ? "тишина — ничего не распознано"
                               : "ошибка распознавания: \(String(errTail.suffix(120)))"
            return
        }
        if wasNote {
            // dictate_note.py печатает JSON {"title": ..., "path": ...}
            if let data = text.data(using: .utf8),
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let title = obj["title"] as? String {
                status = "📝 заметка «\(title)» — в графе и памяти"
            } else {
                status = "📝 заметка сохранена"
            }
            NSSound(named: "Glass")?.play()
        } else if let handler {
            handler(text)
            status = "распознано: \(String(text.prefix(60)))"
            NSSound(named: "Glass")?.play()
        } else {
            insert(text: text)
        }
    }

    // MARK: - Вставка в активное поле

    private func insert(text: String) {
        let pb = NSPasteboard.general
        // сохраняем ВСЕ типы (скриншот/RTF), не только строку — иначе
        // картинка в буфере пропадала после диктовки безвозвратно
        let saved = pb.pasteboardItems?.compactMap { item -> NSPasteboardItem? in
            let copy = NSPasteboardItem()
            for t in item.types {
                if let d = item.data(forType: t) { copy.setData(d, forType: t) }
            }
            return copy.types.isEmpty ? nil : copy
        } ?? []
        pb.clearContents()
        pb.setString(text, forType: .string)
        let ourChange = pb.changeCount

        if AXIsProcessTrusted() {
            let src = CGEventSource(stateID: .combinedSessionState)
            let vDown = CGEvent(keyboardEventSource: src, virtualKey: CGKeyCode(kVK_ANSI_V), keyDown: true)
            vDown?.flags = .maskCommand
            let vUp = CGEvent(keyboardEventSource: src, virtualKey: CGKeyCode(kVK_ANSI_V), keyDown: false)
            vUp?.flags = .maskCommand
            vDown?.post(tap: .cghidEventTap)
            vUp?.post(tap: .cghidEventTap)
            status = "вставлено: \(String(text.prefix(60)))"
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
                // если буфер уже сменил кто-то другой (юзер успел скопировать) — не трогаем
                guard pb.changeCount == ourChange else { return }
                pb.clearContents()
                if !saved.isEmpty { pb.writeObjects(saved) }
            }
        } else {
            // без права Accessibility печатать за пользователя нельзя —
            // текст в буфере, один ⌘V руками
            status = "в буфере — нажми ⌘V (дай Чароиту право Universal Access для автовставки)"
        }
        NSSound(named: "Glass")?.play()
    }
}

/// Кнопка-микрофон у поля ввода: тап — запись, тап — распознанный текст
/// дописывается в биндинг. Локально (GigaAM), как и вся диктовка.
struct DictationButton: View {
    @ObservedObject private var dictation = DictationService.shared
    @Binding var text: String

    var body: some View {
        Button {
            DictationService.shared.toggleInto { spoken in
                text = text.isEmpty ? spoken : text + " " + spoken
            }
        } label: {
            Image(systemName: dictation.isRecording ? "mic.fill" : "mic")
                .font(.system(size: 14))
                .foregroundStyle(dictation.isRecording ? Color.red : Color.secondary)
        }
        .buttonStyle(.plain)
        .help(dictation.isRecording ? "Стоп — распознать и вставить" : "Диктовка (локально, GigaAM)")
    }
}

#endif
