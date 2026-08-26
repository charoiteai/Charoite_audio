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

    /// Облако: панель Claude и всё, что покидает машину.
    static let surfaceCloud = sky.opacity(0.06)
    static let borderCloud = sky.opacity(0.22)

    /// Предупреждение — системный оранжевый, а не фирменный цвет:
    /// «обрати внимание», но ничего не сломано. Просрочка — его частный
    /// случай; один токен вместо `.orange` по месту в одиннадцати вью
    /// (дизайн-аудит 21.08: цвет «по месту» — первый признак, что система
    /// живёт на бумаге, а не в коде).
    static let warning = Color.orange
    static let overdue = warning
}

// MARK: - Поверхности происхождения
//
// Лавандовый контейнер MemorySurface удалён 24.08: владелец выбрал для
// экрана «Память» гамму библиотеки встреч (белая карточка, волосяная
// рамка), происхождение из памяти несут чипы источников и строка мета.
// Токен surfaceMemory остаётся — панель «Ответ по архиву» в суфлёре.

/// Небесная поверхность облака: всё, что уходит с машины. «Локальное
/// молчит, облачное видно» — видно цветом, а не только подписью.
struct CloudSurface<Content: View>: View {
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 6) { content() }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surfaceCloud,
                        in: RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
                    .strokeBorder(Theme.borderCloud, lineWidth: 1)
            }
    }
}

// MARK: - Пустое состояние

/// Пустое состояние говорит, что появится и что нажать.
///
/// Правило 2 ревизии 08.08. До него на пяти экранах жили пять самодельных
/// «иконка `.quaternary` + строка по центру» — ни одно не называло
/// действие. Заголовок, объяснение и одно действие, прижатые к верху и
/// левому краю: пустота читается как начало, а не как поломка.
/// Иконка остаётся маленькой и рядом с заголовком — не плакат.
struct EmptyState<Action: View>: View {
    let title: String
    let text: String
    var systemImage: String?
    /// Внутри секции с собственным заголовком внешний отступ лишний.
    var inset: Bool = true
    @ViewBuilder var action: () -> Action

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                if let systemImage {
                    Image(systemName: systemImage)
                        .font(.headline)
                        .foregroundStyle(Theme.accent)
                }
                Text(title).font(.headline)
            }
            Text(text)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            action()
        }
        .frame(maxWidth: .infinity, alignment: .topLeading)
        .padding(.horizontal, inset ? 16 : 0)
        .padding(.top, inset ? 16 : 0)
    }
}

extension EmptyState where Action == EmptyView {
    init(_ title: String, text: String, systemImage: String? = nil, inset: Bool = true) {
        self.init(title: title, text: text, systemImage: systemImage, inset: inset) { EmptyView() }
    }
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
        .accessibilityLabel(Text(title))
        .accessibilityValue(Text(isOn
            ? L.t("Включено", "On", "已开启")
            : L.t("Выключено", "Off", "已关闭")))
        .accessibilityAddTraits(isOn ? .isSelected : [])
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

    /// Маркеры срока, после которых идёт дата. Замер по рабочему графу
    /// (111 открытых поручений, 08.08): «до ДД.ММ» — 1 штука, «к ДД.ММ» — 1,
    /// «дедлайн ДД.ММ» — 1. Три формы вместо одной стоят три строки и
    /// втрое увеличивают покрытие; ради одной формы чип не окупался бы.
    ///
    /// Чего здесь намеренно нет: «до конца августа» — самая частая живая
    /// форма (16 из 111). Её нельзя превратить в дату, не додумав за
    /// человека, а срок в интерфейсе, который врёт, хуже отсутствующего.
    private static let markers = ["до ", "к ", "дедлайн ", "дедлайну "]

    static func parse(_ text: String) -> TaskDue? {
        for marker in markers {
            var searchFrom = text.startIndex
            while let range = text.range(of: marker, range: searchFrom..<text.endIndex) {
                searchFrom = range.upperBound
                // Маркер обязан быть отдельным словом. Без этой проверки «к »
                // совпадает с концом любого слова на «к»: живое поручение
                // «повторить установку в понедельни|к 10.08|» получало срок
                // 10.08, хотя это дата установки, а не дедлайн. Поймано
                // живьём на графе 08.08, не тестом.
                if range.lowerBound > text.startIndex {
                    let before = text[text.index(before: range.lowerBound)]
                    guard !before.isLetter, !before.isNumber else { continue }
                }
                let parts = text[range.upperBound...].prefix(5).split(separator: ".")
                guard parts.count == 2,
                      let day = Int(parts[0]), let month = Int(parts[1]),
                      (1...31).contains(day), (1...12).contains(month)
                else { continue }
                return TaskDue(day: day, month: month)
            }
        }
        return nil
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

/// Одна строка готовности вместо серой подписи у кнопки — от РЕАЛЬНЫХ
/// проверок SetupReadinessService (Python, конфиг, микрофон, системный
/// звук, Ollama, модели, граф), а не от самочувствия интерфейса: прежний
/// вариант с ручными isReady/«N проверки» врал бы до первой попытки
/// запуска (ревью 15.08).
struct ReadinessLine: View {
    let snapshot: SetupReadinessSnapshot?
    var isChecking: Bool = false

    private var dot: Color {
        guard let snapshot else { return .secondary }
        if snapshot.problems > 0 { return Theme.overdue }
        if snapshot.warnings > 0 { return Theme.warning }
        return Theme.ok
    }

    private var title: String {
        guard let snapshot else {
            return L.t("Проверяю готовность…", "Checking readiness…", "正在检查就绪状态…")
        }
        if snapshot.problems > 0 { return L.t("Не готов", "Not ready", "尚未就绪") }
        if snapshot.warnings > 0 {
            return L.t("Готов, с оговорками", "Ready, with warnings", "可以录音（有警告）")
        }
        return L.t("Готов записывать", "Ready to record", "可以录音")
    }

    /// Что именно мешает: заголовок первой блокирующей проверки, иначе
    /// первого предупреждения. Warning — оранжевый, не красный.
    private var limitation: (text: String, tint: Color)? {
        guard let snapshot else { return nil }
        if let blocked = snapshot.checks.first(where: { $0.state == .blocked }) {
            return (blocked.title, Theme.overdue)
        }
        if let warning = snapshot.checks.first(where: { $0.state == .warning }) {
            return (warning.title, Theme.warning)
        }
        return nil
    }

    var body: some View {
        HStack(spacing: 8) {
            if isChecking && snapshot == nil {
                ProgressView().controlSize(.mini)
            } else {
                Circle().fill(dot).frame(width: 7, height: 7)
            }
            Text(title).font(.body.weight(.medium))
            if let snapshot {
                let passed = snapshot.checks.filter { $0.state == .ready }.count
                Text(L.t("\(passed) из \(snapshot.checks.count) проверок",
                         "\(passed) of \(snapshot.checks.count) checks",
                         "\(passed)/\(snapshot.checks.count) 项检查"))
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
            if let limitation {
                Text(limitation.text)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(limitation.tint)
                    .lineLimit(1)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Capsule().fill(limitation.tint.opacity(0.13)))
            }
        }
    }
}

/// Кнопка записи для экрана «Сегодня».
///
/// До записи — фирменный градиент с тенью и честный хоткей; во время —
/// системный красный, живая волна и моноширинный таймер; в переходах —
/// «Запускаю…»/«Останавливаю…» с блокировкой повторного клика. Показывает
/// состояние ЗАПИСИ, а не наличие входного сигнала: метр уровня удалён —
/// уровень входа нигде не публикуется, и полоски были бы вечно пустым
/// враньём; настоящий метр — отдельная задача (ревью 15.08).
/// Секундные часы записи, замкнутые в собственную вью: TimelineView
/// перерисовывает только эти цифры. Секундный @Published в сервисе
/// перерисовывал каждого подписчика целиком (№50 — 37% CPU на записи).
struct RecordingClock: View {
    let startedAt: Date?

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            Text(SuflerService.clockText(
                startedAt.map { context.date.timeIntervalSince($0) } ?? 0))
        }
    }
}

struct RecordCapsule: View {
    let isRecording: Bool
    var isTransitioning: Bool = false
    let clockFrom: Date?
    let action: () -> Void

    /// Один источник для надписи и системного шортката: разъедутся — капсула
    /// снова начнёт рисовать несуществующую комбинацию (до ревью 15.08 на
    /// ней годами висел «⌥⌘R», которого в приложении нет; реальный шорткат
    /// живой кнопки — ⌘⇧Space).
    static let shortcutKey: KeyEquivalent = .space
    static let shortcutModifiers: EventModifiers = [.command, .shift]
    static let shortcutLabel = "⌘⇧␣"

    var body: some View {
        Button(action: action) {
            HStack(spacing: 9) {
                if isTransitioning {
                    ProgressView().controlSize(.small).tint(.white)
                    Text(isRecording
                         ? L.t("Останавливаю…", "Stopping…", "正在停止…")
                         : L.t("Запускаю…", "Starting…", "正在启动…"))
                        .font(.caption.weight(.semibold))
                } else if isRecording {
                    Image(systemName: "waveform")
                        // живая волна, как у кнопки суфлёра: видно СРАЗУ,
                        // что слушаем, без мигающих лампочек
                        .symbolEffect(.variableColor.iterative,
                                      options: .repeating, isActive: true)
                    RecordingClock(startedAt: clockFrom)
                        .font(.body.weight(.light))
                        .monospacedDigit()
                    Divider().frame(height: 14).overlay(Color.white.opacity(0.35))
                    Text(L.t("Стоп", "Stop", "停止")).font(.caption.weight(.semibold))
                } else {
                    Image(systemName: "mic")
                    Text(L.t("Слушать встречу", "Listen to the meeting", "旁听会议"))
                        .font(.headline)
                    Text(Self.shortcutLabel)
                        .font(.caption.weight(.medium))
                        .foregroundStyle(.white.opacity(0.66))
                }
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 16)
            .padding(.vertical, 9)
            .background {
                if isRecording || isTransitioning {
                    Capsule().fill(Color.red.opacity(isTransitioning ? 0.75 : 1))
                } else {
                    Capsule().fill(Theme.brand)
                }
            }
            .shadow(color: isRecording || isTransitioning
                    ? .clear : Theme.accent.opacity(0.45),
                    radius: 9, y: 4)
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .disabled(isTransitioning)
        .keyboardShortcut(Self.shortcutKey, modifiers: Self.shortcutModifiers)
    }
}

#endif
