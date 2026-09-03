import AppKit
import AVFoundation
import Carbon.HIToolbox
import SwiftUI

#if os(macOS)

/// Локальная диктовка: ⌥⌘D — старт/стоп записи, GigaAM распознаёт на
/// устройстве, текст вставляется в активное поле (⌘V с восстановлением
/// буфера). Ничего не покидает мак — наш ответ облачному Wispr Flow.
///
/// Пока человек говорит, системный движок (macOS 26+) показывает живой
/// черновик на плашке внизу экрана — `LiveDictationPreview`. Черновик
/// хуже GigaAM (12,4 % против 2,9 % ошибок на эталоне 02.09), поэтому
/// финальный текст всегда от GigaAM; черновик идёт в дело только если
/// python не ответил — на маке без модели диктовка всё равно работает.
@MainActor
final class DictationService: ObservableObject {
    static let shared = DictationService()

    @Published var isRecording = false
    @Published var status = ""

    private var proc: Process?
    private var stdinPipe: Pipe?
    private var hotKeyRef: EventHotKeyRef?
    private var noteHotKeyRef: EventHotKeyRef?
    private var diaryHotKeyRef: EventHotKeyRef?
    /// Задан — распознанный текст уходит сюда (кнопка-микрофон в чате),
    /// иначе — системная вставка в активное поле (глобальный ⌥⌘D)
    private var onResult: ((String) -> Void)?
    /// Режим голосовой заметки: dictate_note.py сам обрабатывает (qwen),
    /// и сохраняет в граф — Swift только показывает итог
    private var noteMode = false
    private var errTail = ""  // хвост stderr питона — для внятной ошибки
    private var autoStop: Task<Void, Never>?  // предохранитель забытой записи
    /// Второй захват микрофона — только ради черновика: python пишет свой
    /// поток через PortAudio, CoreAudio отдаёт вход обоим. Упал этот —
    /// диктовка идёт как раньше, без плашки.
    private var previewEngine: AVAudioEngine?
    private var previewBox: AnyObject?          // LiveDictationPreview (macOS 26+)
    private var previewTask: Task<Void, Never>?
    /// Финализация черновика после стопа: `finished()` ждёт её только когда
    /// черновик нужен как страховка. Каждая диктовка начинает с nil — чужой
    /// черновик прошлой диктовки в эту не попадёт.
    private var draftFinish: Task<String, Never>?
    /// Номер диктовки. Доставка результата бывает отложенной (ожидание
    /// черновика), и к её моменту человек мог начать следующую — чужой
    /// текст в чужое поле и чужой статус не попадают.
    private var generation = 0
    private static var previewUnavailableLogged = false

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
                switch id {
                case 2: DictationService.shared.toggleNote()
                case 3: DictationService.shared.toggleDiary()
                default: DictationService.shared.toggle()
                }
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
        // ⌥⌘J — дневник (мысль в Дневник/YYYY-MM-DD.md со связью со встречей)
        let diaryID = EventHotKeyID(signature: OSType(0x4348_5244), id: 3)
        RegisterEventHotKey(UInt32(kVK_ANSI_J), UInt32(cmdKey | optionKey),
                            diaryID, GetApplicationEventTarget(), 0, &diaryHotKeyRef)
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

    /// Дневник: мысль уходит в личную сферу (Дневник/день.md, секция HH:MM)
    /// с идеями, задачами-чекбоксами и ссылкой на сегодняшнюю встречу.
    func toggleDiary() {
        if isRecording {
            stop()
        } else {
            start(script: "src/dictate_note.py", args: ["--diary"], note: true)
        }
    }

    // MARK: - Запись (python: sounddevice + GigaAM, всё локально)

    private func start(script: String = "src/dictate.py", args: [String] = [],
                       note: Bool = false,
                       onResult: ((String) -> Void)? = nil) {
        // proc ещё жив (стоп идёт, распознавание не финишировало) — выходим, НЕ
        // трогая noteMode/onResult in-flight записи. Иначе повторный вызов
        // залипал: чат-диктовка уходила в ветку заметки → «через чат ноль реакции»
        guard proc == nil else { return }
        self.noteMode = note
        self.onResult = onResult
        draftFinish = nil
        generation += 1
        let p = Process()
        p.arguments = [script] + args
        AppSettings.preparePython(p, root: suflerRoot)
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
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.errTail = String((self.errTail + chunk).suffix(500))
                if chunk.contains("REC"), self.isRecording {
                    self.status = L.t("🎙 запись пошла — говори (⌥⌘D — стоп)", "🎙 recording — speak (⌥⌘D to stop)", "🎙 录音中——请讲话（⌥⌘D 停止）")
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
            status = noteMode
                ? (args.contains("--diary") ? L.t("🎙 дневник… говори (⌥⌘J — стоп)", "🎙 diary… speak (⌥⌘J to stop)", "🎙 日记…请讲话（⌥⌘J 停止）")
                                            : L.t("🎙 заметка… говори (⌥⌘N — стоп)", "🎙 note… speak (⌥⌘N to stop)", "🎙 笔记…请讲话（⌥⌘N 停止）"))
                : L.t("🎙 диктовка… (⌥⌘D — стоп)", "🎙 dictation… (⌥⌘D to stop)", "🎙 听写…（⌥⌘D 停止）")
            NSSound(named: "Pop")?.play()
            // Заметка и дневник черновик не берут никогда (текст уходит в
            // граф и память) — незачем платить вторым захватом микрофона.
            if !note { startPreview() }
            // глобальный хоткей легко забыть: не даём писать вечно — часовой
            // wav всё равно не распознается, а микрофон «висит» открытым
            autoStop = Task { [weak self] in
                try? await Task.sleep(for: .seconds(600))
                guard let self, self.isRecording, !Task.isCancelled else { return }
                self.stop()
                self.status = L.t("диктовка остановлена сама через 10 минут — распознаю…", "dictation auto-stopped after 10 minutes — transcribing…", "听写已在 10 分钟后自动停止——正在识别…")
            }
        } catch {
            status = L.t("диктовка не запустилась: \(error.localizedDescription)", "dictation failed to start: \(error.localizedDescription)", "听写未能启动：\(error.localizedDescription)")
            // залипший колбэк уводил бы следующую ГЛОБАЛЬНУЮ диктовку в невидимый биндинг
            // (self. — параметр onResult затеняет свойство)
            self.onResult = nil
            self.noteMode = false
        }
    }

    private func stop() {
        autoStop?.cancel()
        autoStop = nil
        status = L.t("распознаю…", "transcribing…", "正在识别…")
        isRecording = false
        stopPreview()
        try? stdinPipe?.fileHandleForWriting.close()  // EOF = стоп записи
        // Распознавание короткое; зависший процесс добьём — и обязательно
        // с SIGKILL следом. Python, застрявший в нативном вызове (NeMo,
        // PortAudio), обработает SIGTERM только по выходе из C-кода, то есть
        // никогда: terminationHandler не вызывался, proc оставался не-nil, и
        // start() навсегда упирался в `guard proc == nil`. ⌥⌘D, ⌥⌘N, ⌥⌘J и
        // кнопка-микрофон переставали отвечать до перезапуска приложения, а
        // индикатор микрофона в статус-баре продолжал гореть.
        let p = proc
        DispatchQueue.global().asyncAfter(deadline: .now() + 25) {
            guard let p, p.isRunning else { return }
            p.terminate()
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + 35) {
            guard let p, p.isRunning else { return }
            kill(p.processIdentifier, SIGKILL)
            Task { @MainActor [weak self] in
                self?.proc = nil
                self?.stdinPipe = nil
                self?.isRecording = false
                self?.status = L.t("распознавание зависло — процесс остановлен",
                                   "recognition hung — process killed",
                                   "识别卡住——进程已终止")
            }
        }
    }

    private func finished(text: String, exit: Int32) {
        // Таймер автостопа снимался только в stop(). Если процесс завершался
        // сам (краш распознавателя, свой таймаут), таск оставался спать — и
        // через десять минут просыпался уже посреди СЛЕДУЮЩЕЙ диктовки,
        // обрывая её со статусом про «10 минут», которых не было.
        autoStop?.cancel()
        autoStop = nil
        proc = nil
        stdinPipe = nil
        isRecording = false
        let handler = onResult
        onResult = nil
        let wasNote = noteMode
        noteMode = false
        // python мог выйти сам (краш, нет модели) — без stop(): движок
        // черновика и микрофон разбираем здесь, иначе захват живёт до выхода
        // приложения, а каждая следующая диктовка плодит нового сироту.
        stopPreview()
        DictationPreviewPanel.shared.hide()
        let finish = draftFinish
        draftFinish = nil
        if text.isEmpty, exit != 0, !wasNote, let finish {
            // GigaAM не ответил (python запустился, но упал: нет модели,
            // сломанный импорт — или его добил сторож: десять минут речи при
            // 26x это ~23 с распознавания, порог сторожа 25 с) — отдаём
            // черновик системного движка: хуже по терминам, но лучше пустого
            // поля, а чей это текст, говорят статус и плашка. Финализация
            // черновика асинхронна и на быстром падении python обычно ещё
            // идёт — ждём её, но недолго. Заметку из черновика не делаем: её
            // текст уходит в граф и в память, а там точность важнее
            // мгновенности.
            status = L.t("GigaAM не ответил — собираю черновик системного движка…",
                         "GigaAM did not answer — collecting the system engine's draft…",
                         "GigaAM 未响应——正在收集系统引擎的草稿…")
            let generation = self.generation
            Task { [weak self] in
                let draft = await Self.awaitDraft(finish, timeout: .seconds(8))
                // Человек мог начать следующую диктовку — её поле и статус не
                // трогаем, устаревший результат пропадает.
                guard let self, self.generation == generation else { return }
                self.deliver(text: draft, exit: exit, fromDraft: !draft.isEmpty,
                             wasNote: wasNote, handler: handler)
            }
            return
        }
        deliver(text: text, exit: exit, fromDraft: false, wasNote: wasNote, handler: handler)
    }

    /// Черновик или пусто, если финализация не уложилась в срок.
    ///
    /// Не TaskGroup: группа на выходе дожидается всех детей, а ребёнка на
    /// `finish.value` отмена не прерывает — потолка не было бы вовсе (второй
    /// круг #486). Здесь ждём первого из двух; финализация, не успевшая к
    /// сроку, доделается в фоне сама и никого не держит.
    private static func awaitDraft(_ finish: Task<String, Never>, timeout: Duration) async -> String {
        let (first, arrive) = AsyncStream<String>.makeStream()
        Task { arrive.yield(await finish.value) }
        let timer = Task { try? await Task.sleep(for: timeout); arrive.yield("") }
        var results = first.makeAsyncIterator()
        let draft = await results.next() ?? ""
        timer.cancel()
        arrive.finish()
        return draft
    }

    private func deliver(text: String, exit: Int32, fromDraft: Bool, wasNote: Bool,
                         handler: ((String) -> Void)?) {
        guard !text.isEmpty else {
            status = exit == 0 ? L.t("тишина — ничего не распознано", "silence — nothing recognized", "静音——未识别到内容")
                               : L.t("ошибка распознавания: \(String(errTail.suffix(120)))", "recognition error: \(String(errTail.suffix(120)))", "识别错误：\(String(errTail.suffix(120)))")
            return
        }
        if fromDraft {
            status = L.t("GigaAM не ответил — вставлен черновик системного движка", "GigaAM did not answer — inserted the system engine's draft", "GigaAM 未响应——已插入系统引擎的草稿")
            // Человек смотрит в чужое поле, а не в строку статуса Чароита:
            // плашка внизу экрана несколько секунд говорит, чей это текст.
            DictationPreviewPanel.shared.flash(text: text, hint: status, seconds: 5)
        }
        if wasNote {
            // dictate_note.py печатает JSON {"title": ..., "path": ...}
            if let data = text.data(using: .utf8),
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let title = obj["title"] as? String {
                status = L.t("📝 заметка «\(title)» — в графе и памяти", "📝 note \u{201C}\(title)\u{201D} — in the graph and memory", "📝 笔记「\(title)」——已入图谱与记忆")
            } else {
                status = L.t("📝 заметка сохранена", "📝 note saved", "📝 笔记已保存")
            }
            NSSound(named: "Glass")?.play()
        } else if let handler {
            handler(text)
            if !fromDraft {
                status = L.t("распознано: \(String(text.prefix(60)))", "recognized: \(String(text.prefix(60)))", "已识别：\(String(text.prefix(60)))")
            }
            NSSound(named: "Glass")?.play()
        } else {
            insert(text: text, keepStatus: fromDraft)
        }
    }

    // MARK: - Живой черновик (системный движок, macOS 26+)

    private func startPreview() {
        guard #available(macOS 26.0, *) else { return }
        previewTask?.cancel()
        previewTask = Task { [weak self] in
            guard let self else { return }
            let live = LiveDictationPreview(locale: Self.previewLocale)
            let ready = await live.prepare()
            guard !Task.isCancelled, self.isRecording else { return }
            guard ready == .ready else {
                if !Self.previewUnavailableLogged {
                    Self.previewUnavailableLogged = true
                    NSLog("[Dictation] живой черновик недоступен: \(ready) для \(Self.previewLocale.identifier)")
                }
                return
            }
            let engine = AVAudioEngine()
            let input = engine.inputNode
            let format = input.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else { return }
            do {
                try await live.start(inputFormat: format)
            } catch {
                NSLog("[Dictation] живой черновик не поднялся: \(error.localizedDescription)")
                live.cancel()
                return
            }
            guard !Task.isCancelled, self.isRecording else { live.cancel(); return }
            live.onChange = { [weak self] text in
                guard let self, self.isRecording else { return }
                DictationPreviewPanel.shared.show(
                    text: text,
                    hint: L.t("черновик системного движка — итог распознает GigaAM после стопа",
                              "system engine draft — GigaAM produces the final text after stop",
                              "系统引擎草稿——停止后由 GigaAM 生成最终文本"))
            }
            input.installTap(onBus: 0, bufferSize: 4096, format: format) { buffer, _ in
                live.ingest(buffer)
            }
            do {
                try engine.start()
            } catch {
                input.removeTap(onBus: 0)
                live.cancel()
                NSLog("[Dictation] микрофон для черновика не открылся: \(error.localizedDescription)")
                return
            }
            self.previewEngine = engine
            self.previewBox = live
        }
    }

    private func stopPreview() {
        previewTask?.cancel()
        previewTask = nil
        guard let engine = previewEngine else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        previewEngine = nil
        guard #available(macOS 26.0, *), let live = previewBox as? LiveDictationPreview else {
            previewBox = nil
            return
        }
        previewBox = nil
        // Финал черновика приходит асинхронно; GigaAM за это время как раз
        // распознаёт. finished() дождётся этой задачи, только если черновик
        // понадобится как страховка.
        draftFinish = Task { await live.finish() }
    }

    /// Язык черновика = язык продукта (sufler.language), как и у GigaAM.
    private static var previewLocale: Locale {
        switch AppSettings.uiLanguage {
        case "en": return Locale(identifier: "en-US")
        case "zh": return Locale(identifier: "zh-CN")
        default: return Locale(identifier: "ru-RU")
        }
    }

    // MARK: - Вставка в активное поле

    private func insert(text: String, keepStatus: Bool = false) {
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
            if !keepStatus {
                status = L.t("вставлено: \(String(text.prefix(60)))", "inserted: \(String(text.prefix(60)))", "已插入：\(String(text.prefix(60)))")
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
                // если буфер уже сменил кто-то другой (юзер успел скопировать) — не трогаем
                guard pb.changeCount == ourChange else { return }
                pb.clearContents()
                if !saved.isEmpty { pb.writeObjects(saved) }
            }
        } else {
            // без права Accessibility печатать за пользователя нельзя —
            // текст в буфере, один ⌘V руками
            status = L.t("в буфере — нажми ⌘V (дай Чароиту право Universal Access для автовставки)", "copied — press ⌘V (grant Charoite the Accessibility right for auto-paste)", "已复制——按 ⌘V（授予 Charoite 辅助功能权限可自动粘贴）")
            if keepStatus {
                // Человек вставит текст сам — он обязан знать, что это черновик.
                status = L.t("черновик системного движка (GigaAM не ответил) ", "system engine draft (GigaAM did not answer) ", "系统引擎草稿（GigaAM 未响应）") + status
            }
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
                .font(.body)
                .foregroundStyle(dictation.isRecording ? Color.red : Color.secondary)
        }
        .buttonStyle(.plain)
        .help(dictation.isRecording ? L.t("Стоп — распознать и вставить", "Stop — transcribe and insert", "停止——识别并插入") : L.t("Диктовка (локально, GigaAM)", "Dictation (local, GigaAM)", "听写（本地，GigaAM）"))
    }
}

#endif
