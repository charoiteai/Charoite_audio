import AppKit
import SwiftUI

#if os(macOS)

// MARK: - Поверхность = происхождение
//
// Правило ревизии интерфейса (docs/design/UI_REVISION_2026-08.md):
// белая системная поверхность — то, что происходит сейчас и локально;
// лавандовая — память (архив, граф, досье); небесная — всё, что уходит
// с этой машины. Палитра та же, что в Theme: новых цветов нет,
// только осмысленные заливки существующих токенов.

extension Theme {
    /// Память: архив встреч, граф, досье.
    static let surfaceMemory = accent.opacity(0.05)
    static let borderMemory = accent.opacity(0.14)

    /// Облако: панель Claude и всё, что покидает машину.
    static let surfaceCloud = sky.opacity(0.06)
    static let borderCloud = sky.opacity(0.22)

    /// Просрочка — системный оранжевый, а не фирменный цвет.
    static let overdue = Color.orange
}

// MARK: - Заголовок панели

/// Иконка, капсовое имя, счётчик и место под одно действие.
///
/// Раньше заголовок панели был неотличим от подписи внутри неё: и то и
/// другое — `.caption` вторичным цветом. Счётчик снимает вопрос «тут
/// пусто или ещё не посчитано».
struct PaneHeader<Trailing: View>: View {
    let title: String
    let systemImage: String
    var count: Int?
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: systemImage)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(title.uppercased())
                .font(.caption2.weight(.semibold))
                .kerning(0.8)
                .foregroundStyle(.secondary)
            if let count, count > 0 {
                Text("\(count)")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(Theme.accent)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Capsule().fill(Theme.accent.opacity(0.10)))
            }
            Spacer(minLength: 8)
            trailing()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(.bar)
        .overlay(alignment: .bottom) { Divider() }
    }
}

extension PaneHeader where Trailing == EmptyView {
    init(_ title: String, systemImage: String, count: Int? = nil) {
        self.init(title: title, systemImage: systemImage, count: count) { EmptyView() }
    }
}

// MARK: - Пустое состояние

/// Что здесь появится, когда — и что нажать сейчас.
///
/// Прижато к верху и выровнено по левому краю: в панели высотой 800
/// точек иконка с одной строкой по центру читалась как «ничего нет»,
/// а не «ещё не началось».
struct CharoiteEmptyState<Action: View>: View {
    let title: String
    let explanation: String
    var shortcut: String?
    @ViewBuilder var action: () -> Action

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(.primary)
            Text(explanation)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let shortcut {
                Text(shortcut)
                    .font(.callout.weight(.medium))
                    .foregroundStyle(Theme.accent)
            }
            action()
        }
        .frame(maxWidth: 520, alignment: .leading)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 18)
        .padding(.top, 20)
    }
}

extension CharoiteEmptyState where Action == EmptyView {
    init(title: String, explanation: String, shortcut: String? = nil) {
        self.init(title: title, explanation: explanation, shortcut: shortcut) { EmptyView() }
    }
}

// MARK: - Слои встречи

/// Чип слоя вместо системного свитча.
///
/// Четыре `Toggle(.switch)` в одну строку красили полосу в системный
/// синий и спорили с фирменной кнопкой записи. Чип несёт цвет слоя:
/// индиго — локальное, `sky` — облачное.
struct LayerChip: View {
    let title: String
    @Binding var isOn: Bool
    var tint: Color = Theme.accent

    var body: some View {
        Button {
            isOn.toggle()
        } label: {
            HStack(spacing: 6) {
                if tint != Theme.accent {
                    Circle()
                        .strokeBorder(tint, lineWidth: 1.5)
                        .frame(width: 7, height: 7)
                }
                Text(title)
                    .font(.caption.weight(isOn ? .semibold : .regular))
            }
            .foregroundStyle(isOn ? tint : Color.secondary)
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(Capsule().fill(isOn ? tint.opacity(0.13) : Color.clear))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .help(isOn ? L.t("Выключить слой", "Turn the layer off", "关闭该层")
                   : L.t("Включить слой", "Turn the layer on", "开启该层"))
    }
}

/// Капсула-контейнер для чипов слоёв.
struct LayerBar<Content: View>: View {
    @ViewBuilder var content: () -> Content

    var body: some View {
        HStack(spacing: 3) { content() }
            .padding(3)
            .background(Capsule().fill(Color(nsColor: .controlBackgroundColor)))
            .overlay(Capsule().strokeBorder(Color.primary.opacity(0.06)))
    }
}

// MARK: - Тезисы

/// Тип тезиса словом, а не эмодзи-префиксом.
enum ThesisKind {
    case decision
    case memory
    case thought

    init(text: String) {
        if text.hasPrefix("📌") || text.hasPrefix("💎") {
            self = .decision
        } else if text.hasPrefix("⏮") {
            self = .memory
        } else {
            self = .thought
        }
    }

    var label: String {
        switch self {
        case .decision: return L.t("Решение", "Decision", "决定")
        case .memory: return L.t("Из памяти", "From memory", "来自记忆")
        case .thought: return L.t("Мысль", "Thought", "想法")
        }
    }

    var tint: Color {
        switch self {
        case .decision: return .orange
        case .memory: return Theme.sky
        case .thought: return .secondary
        }
    }

    /// Текст без служебного знака: знак уходит в чип, в markdown остаётся.
    static func strip(_ text: String) -> String {
        var body = Substring(text)
        for mark in ["📌", "💎", "⏮"] where body.hasPrefix(mark) {
            body = body.dropFirst(mark.count)
        }
        return String(body).trimmingCharacters(in: .whitespaces)
    }
}

/// Карточка тезиса: чип типа, время, текст и — для памяти — источник.
struct CharoiteThesisCard: View {
    let text: String
    var time: String?
    var source: String?

    var body: some View {
        let kind = ThesisKind(text: text)
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text(kind.label.uppercased())
                    .font(.system(size: 9.5, weight: .bold))
                    .kerning(0.6)
                    .foregroundStyle(kind.tint)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 3)
                    .background(RoundedRectangle(cornerRadius: 4).fill(kind.tint.opacity(0.14)))
                if let time {
                    Text(time)
                        .font(.caption2)
                        .monospacedDigit()
                        .foregroundStyle(.tertiary)
                }
            }
            Text(ThesisKind.strip(text))
                .font(.callout)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            if let source {
                Text(source)
                    .font(.caption2)
                    .foregroundStyle(kind.tint)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 11)
        .padding(.vertical, 9)
        .background(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
            .fill(kind.tint.opacity(0.055)))
        .overlay(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
            .strokeBorder(kind.tint.opacity(0.22)))
    }
}

// MARK: - Срок поручения

/// Срок из текста поручения вида «… — до 24.07».
///
/// Markdown остаётся источником истины: срок не переносится в базу,
/// а читается из строки каждый раз.
struct TaskDue: Equatable {
    let day: Int
    let month: Int

    enum Status: Equatable {
        case overdue(days: Int)
        case soon(days: Int)
        case later
    }

    static func parse(_ text: String) -> TaskDue? {
        guard let marker = text.range(of: "до ") else { return nil }
        let tail = text[marker.upperBound...].prefix(5)
        let parts = tail.split(separator: ".")
        guard parts.count == 2,
              let day = Int(parts[0]), let month = Int(parts[1]),
              (1...31).contains(day), (1...12).contains(month)
        else { return nil }
        return TaskDue(day: day, month: month)
    }

    func status(now: Date = Date(), calendar: Calendar = .current) -> Status {
        var components = calendar.dateComponents([.year], from: now)
        components.day = day
        components.month = month
        guard var target = calendar.date(from: components) else { return .later }
        var days = calendar.dateComponents([.day], from: calendar.startOfDay(for: now),
                                           to: calendar.startOfDay(for: target)).day ?? 0
        // В тексте поручения года нет. «до 15.01», прочитанное в августе, —
        // это следующий январь, а не просрочка на двести дней: всё, что
        // дальше полугода в прошлом, относим к будущему году.
        if days < -183, let next = calendar.date(byAdding: .year, value: 1, to: target) {
            target = next
            days = calendar.dateComponents([.day], from: calendar.startOfDay(for: now),
                                           to: calendar.startOfDay(for: target)).day ?? 0
        }
        if days < 0 { return .overdue(days: -days) }
        if days <= 7 { return .soon(days: days) }
        return .later
    }

    var short: String { String(format: "%02d.%02d", day, month) }
}

/// Срок чипом справа: просрочка — оранжевая, близкий срок — обычный.
struct DueChip: View {
    let due: TaskDue

    var body: some View {
        let status = due.status()
        Text(label(status))
            .font(.caption2.weight(.semibold))
            .monospacedDigit()
            .foregroundStyle(tint(status))
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(Capsule().fill(tint(status).opacity(0.13)))
    }

    private func label(_ status: TaskDue.Status) -> String {
        switch status {
        case .overdue(let days):
            return L.t("просрочено · \(due.short)",
                       "overdue · \(due.short)",
                       "已逾期 · \(due.short)") + " (\(days))"
        case .soon:
            return L.t("до \(due.short)", "due \(due.short)", "截止 \(due.short)")
        case .later:
            return L.t("до \(due.short)", "due \(due.short)", "截止 \(due.short)")
        }
    }

    private func tint(_ status: TaskDue.Status) -> Color {
        switch status {
        case .overdue: return Theme.overdue
        case .soon: return .primary
        case .later: return .secondary
        }
    }
}

// MARK: - Готовность и запись

/// Одна строка готовности вместо серой подписи у кнопки.
struct ReadinessLine: View {
    let isReady: Bool
    let checksPassed: Int
    var limitation: String?

    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(isReady ? Theme.ok : Theme.overdue)
                .frame(width: 7, height: 7)
            Text(isReady
                 ? L.t("Готов записывать", "Ready to record", "可以录音")
                 : L.t("Не готов", "Not ready", "尚未就绪"))
                .font(.system(size: 13, weight: .medium))
            Text(L.t("\(checksPassed) проверки", "\(checksPassed) checks", "\(checksPassed) 项检查"))
                .font(.caption)
                .foregroundStyle(.tertiary)
            if let limitation {
                Text(limitation)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(Theme.overdue)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Capsule().fill(Theme.overdue.opacity(0.13)))
            }
        }
    }
}

/// Кнопка записи, которая показывает, что вас слышно.
///
/// До записи — фирменный градиент с тенью; во время — системный красный
/// без тени, моноширинный таймер и метр уровня входа.
struct RecordCapsule: View {
    let isRecording: Bool
    let clock: String
    /// Уровни входа 0…1, обычно 5–7 значений.
    let levels: [CGFloat]
    let action: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Button(action: action) {
                HStack(spacing: 9) {
                    if isRecording {
                        Circle().fill(.white).frame(width: 8, height: 8)
                        Text(clock)
                            .font(.system(size: 14, weight: .light))
                            .monospacedDigit()
                        Divider().frame(height: 14).overlay(Color.white.opacity(0.35))
                        Text(L.t("Стоп", "Stop", "停止")).font(.caption.weight(.semibold))
                    } else {
                        Image(systemName: "mic")
                        Text(L.t("Слушать встречу", "Listen to the meeting", "旁听会议"))
                            .font(.system(size: 13.5, weight: .semibold))
                        Text("⌥⌘R")
                            .font(.caption.weight(.medium))
                            .foregroundStyle(.white.opacity(0.66))
                    }
                }
                .foregroundStyle(.white)
                .padding(.horizontal, 16)
                .padding(.vertical, 9)
                .background {
                    if isRecording {
                        Capsule().fill(Color.red)
                    } else {
                        Capsule().fill(Theme.brand)
                    }
                }
                .shadow(color: isRecording ? .clear : Theme.accent.opacity(0.45),
                        radius: 9, y: 4)
                .contentShape(Capsule())
            }
            .buttonStyle(.plain)

            if isRecording {
                HStack(alignment: .bottom, spacing: 2) {
                    ForEach(Array(levels.enumerated()), id: \.offset) { _, level in
                        Capsule()
                            .fill(level > 0.55 ? Theme.ok : Color.secondary.opacity(0.55))
                            .frame(width: 3, height: max(4, level * 17))
                    }
                }
                .frame(height: 18)
                .accessibilityLabel(L.t("Уровень входа", "Input level", "输入电平"))
            }
        }
    }
}

#endif
