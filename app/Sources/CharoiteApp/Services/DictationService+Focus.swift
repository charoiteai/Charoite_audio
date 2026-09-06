#if os(macOS)
import AppKit
import ApplicationServices

/// Чистые швы диктовки: чтение фокуса через Accessibility, матрица решений
/// о вставке, поколения, парковка текста. Вынесены из DictationService.swift
/// (SwiftLint file_length, круг 4 по #517): всё здесь — nonisolated static,
/// без состояния сервиса, и именно это покрывают тесты
/// DictationPasteTargetTests.
extension DictationService {
    nonisolated static func focusInfo(secureOnly: Bool = false) -> FocusInfo {
        var info = FocusInfo()
        guard AXIsProcessTrusted() else { return info }
        let system = AXUIElementCreateSystemWide()
        AXUIElementSetMessagingTimeout(system, 0.25)   // зависшее чужое приложение не держит старт диктовки
        var focused: CFTypeRef?
        var err = AXUIElementCopyAttributeValue(system, kAXFocusedUIElementAttribute as CFString, &focused)
        // Определённое «сфокусированного элемента нет» фолбэка не требует —
        // лишний IPC и шанс ухудшить ответ до «неизвестно» (GLM r12 M2)
        if Self.needsFrontmostFallback(err), let pid = NSWorkspace.shared.frontmostApplication?.processIdentifier {
            // Chromium (Яндекс Браузер, Chrome) без включённой accessibility
            // на общесистемный запрос отвечает cannotComplete, а на прямой —
            // «нет сфокусированного элемента»; так прячущее свои поля
            // приложение отличается от зависшего (замер 04.09, GLM r11 I1)
            let app = AXUIElementCreateApplication(pid)
            AXUIElementSetMessagingTimeout(app, 0.25)
            err = AXUIElementCopyAttributeValue(app, kAXFocusedUIElementAttribute as CFString, &focused)
        }
        guard err == .success, let value = focused, CFGetTypeID(value) == AXUIElementGetTypeID() else {
            info.security = Self.focusSecurity(focused: err, role: .failure, roleName: nil, subrole: nil)
            info.blind = err == .noValue
            return info
        }
        let field = unsafeBitCast(value, to: AXUIElement.self)
        AXUIElementSetMessagingTimeout(field, 0.25)   // таймаут — свойство ссылки, не наследуется
        var pid: pid_t = 0
        if AXUIElementGetPid(field, &pid) == .success, pid > 0 { info.pid = pid }
        var role: CFTypeRef?
        var subrole: CFTypeRef?
        let roleErr = AXUIElementCopyAttributeValue(field, kAXRoleAttribute as CFString, &role)
        AXUIElementCopyAttributeValue(field, kAXSubroleAttribute as CFString, &subrole)
        info.security = Self.focusSecurity(focused: .success, role: roleErr,
                                           roleName: role as? String, subrole: subrole as? String)
        if secureOnly { return info }   // сторожу окно не нужно — минус один запрос по 0,25 с (GLM I1)
        var window: CFTypeRef?
        if AXUIElementCopyAttributeValue(field, kAXWindowAttribute as CFString, &window) == .success,
           let w = window, CFGetTypeID(w) == AXUIElementGetTypeID() {
            info.window = unsafeBitCast(w, to: AXUIElement.self)
        }
        return info
    }

    /// Ответы AX → что известно о поле: «нет сфокусированного элемента» —
    /// пароля нет; роль прочитана — по роли и подроли; не ответило
    /// (таймаут, зависшее приложение) — неизвестно. Чистая функция для теста.
    /// Решение 04.09 (запись в decisions): Chromium с выключенной
    /// accessibility отвечает «нет элемента» и на обычное поле, и на пароль —
    /// это «пароль неотличим», и мы сознательно считаем его «нет пароля»:
    /// иначе живая плашка гасла бы в главном браузере, а защиты там всё
    /// равно не дать. Без права Accessibility наоборот fail-closed: там нет
    /// и вставки, плашка была бы голым показом.
    nonisolated static func focusSecurity(focused: AXError, role: AXError,
                                          roleName: String?, subrole: String?) -> FocusSecurity {
        if focused == .noValue { return .clear }
        guard focused == .success, role == .success else { return .unknown }
        return roleName == "AXSecureTextField" || subrole == "AXSecureTextField" ? .secure : .clear
    }

    /// Идти ли за фокусом к фронтмост-приложению напрямую: только когда
    /// общесистемный запрос не ответил; определённое «элемента нет»
    /// (noValue) фолбэка не требует (GLM r12 M2, r13 M2).
    nonisolated static func needsFrontmostFallback(_ err: AXError) -> Bool { err != .success && err != .noValue }

    /// Поле под фокусом без окна — сторожу и живой плашке окно не нужно
    /// (минус один запрос по 0,25 с, GLM I1).
    nonisolated static func focusSecurityNow() -> FocusSecurity { focusInfo(secureOnly: true).security }

    /// Якорь вставки: владелец сфокусированного элемента (неактивирующая
    /// key-панель — строка меню Чароита — не меняет frontmostApplication,
    /// а окно и pid должны быть из одного источника; круг 2 по #488, DS),
    /// иначе приложение впереди.
    nonisolated static func frontAnchor(_ info: FocusInfo) -> PasteAnchor? {
        let owner = info.pid.flatMap { NSRunningApplication(processIdentifier: $0) }
            ?? NSWorkspace.shared.frontmostApplication
        guard let owner else { return nil }
        // Окно — только от того же процесса, что и владелец якоря: pid из AX,
        // не резолвящийся в живое приложение, оставил бы окно мёртвого
        // процесса при pid фронтмоста (DS r3 M1, r6 M3)
        return PasteAnchor(pid: owner.processIdentifier, name: owner.localizedName ?? "?",
                           window: info.pid == owner.processIdentifier ? info.window : nil)
    }

    /// Итог доставки: право Accessibility → пароль → окно. Диктовка,
    /// касавшаяся поля пароля, автовставки не получает вообще — ни в обычное
    /// поле, ни в само поле пароля: поле маскирует ввод, но статус и плашка
    /// Чароита не маскируют, а synthetic ⌘V под secure input глотается и
    /// статус «вставлено» врёт (DS r7 C1/C2/I1 — откат advisory r6).
    nonisolated static func finalDecision(trusted: Bool, own: pid_t, startedIn: PasteAnchor?, now: PasteAnchor?,
                                          secureSeen: Bool, nowSecure: Bool) -> PasteDecision {
        guard trusted else { return .noAccessibility }
        // Пароль под фокусом доставки — тоже .secret: ⌘V под secure input
        // глотается, статус «вставлено» врал бы (DS r7 I1, r8 I2)
        if secureSeen || nowSecure { return .secret }
        return pasteDecision(trusted: true, own: own, startedIn: startedIn, now: now)
    }

    /// Применять ли результат фонового чтения фокуса: только к той же
    /// диктовке и пока она пишется (DS r11 M1, M3 — чистый шов для теста).
    nonisolated static func secureReadApplies(generation: Int, current: Int, recording: Bool) -> Bool {
        generation == current && recording
    }

    /// Показывать ли надиктованный текст на плашке: только по чтению AX,
    /// ответившему «не пароль» — не в поле пароля, не после него (защёлка
    /// держит до конца диктовки, DS r6 C1, M2) и не когда приложение не
    /// ответило (GLM r11 I1); без права Accessibility пароль не отличить —
    /// плашки нет вовсе (DS r8 I1).
    nonisolated static func liveStripAllowed(security: FocusSecurity, secureSeen: Bool, trusted: Bool) -> Bool {
        trusted && !secureSeen && security == .clear
    }

    /// Плашка «в поле вставлен черновик» без текста — только когда вставка
    /// реально идёт (право Accessibility есть), приложение впереди не ответило
    /// и пароля не было (GLM r13 I1, r14 M2).
    nonisolated static func unknownFlashAllowed(trusted: Bool, security: FocusSecurity, secureSeen: Bool) -> Bool {
        trusted && security == .unknown && !secureSeen
    }

    /// Куда уйдёт результат: ⌘V только с правом Accessibility и только туда,
    /// где нажали ⌥⌘D — то же приложение и (если оба окна известны) то же
    /// окно. Якорь неизвестен или это сам Чароит (строка меню-бара, чат) —
    /// ведём себя как раньше, вставляем в активное поле; впереди сам Чароит
    /// при чужом якоре — текст в буфере, в собственную панель не вставляем.
    nonisolated static func pasteDecision(trusted: Bool, own: pid_t,
                                          startedIn: PasteAnchor?, now: PasteAnchor?) -> PasteDecision {
        guard trusted else { return .noAccessibility }
        guard let startedIn, startedIn.pid != own, let now else { return .paste }
        if now.pid != startedIn.pid { return .windowChanged }
        if let a = startedIn.window, let b = now.window, !CFEqual(a, b) { return .windowChanged }
        return .paste
    }

    /// Последовательные очереди AX-чтений: старт и доставка — на одной (в
    /// зависшее приложение впереди не уходят параллельные запросы по
    /// 0,25–1,25 с, DS r1 M2), сторож пароля — на своей, чтобы чтение доставки
    /// не задерживало защёлку (DS r2 M4). В полёте не больше двух.
    nonisolated static let focusQueue = DispatchQueue(label: "ai.charoite.dictation.focus", qos: .userInitiated)
    nonisolated static let watchQueue = DispatchQueue(label: "ai.charoite.dictation.watch", qos: .userInitiated)

    /// Чтение фокуса вне главного потока с ожиданием из async-контекста:
    /// межпроцессный вызов в чужое приложение (до 0,5 с на зависшем) главный
    /// поток не держит ни на старте, ни на доставке (№161, GLM r11 крит.2 по
    /// #488). Потолок ожидания — таймауты AX внутри focusInfo.
    nonisolated static func focusOffMain(secureOnly: Bool) async -> FocusInfo {
        await withCheckedContinuation { cont in
            focusQueue.async { cont.resume(returning: focusInfo(secureOnly: secureOnly)) }
        }
    }

    /// Та ли это ещё диктовка: стартовое чтение применяется только к своей
    /// (следующая берёт свой якорь и защёлку), доставка исполняется только
    /// пока не началась следующая (иначе её поле, статус и защёлка получили
    /// бы чужой текст и чужое чтение). Один предикат на оба шва (DS r2 M1).
    nonisolated static func sameDictation(generation: Int, current: Int) -> Bool {
        generation == current
    }
}
#endif
