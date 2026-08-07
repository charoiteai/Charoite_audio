import SwiftUI

#if os(macOS)

// MARK: - Роль кнопки
//
// В приложении 48 вызовов buttonStyle четырьмя системными стилями:
// .plain (30), .link (9), .borderedProminent (6), .borderless (3). Тинт
// задан у двух заметных из шести — остальные системно-синие рядом с
// фирменным индиго. Роль кнопки должна читаться из вида, а не из того,
// какой системный стиль оказался под рукой.
//
// Правила (docs/design/BUTTONS_2026-08.md):
//   1. Одна заметная на панель.
//   2. .plain больше не стиль кнопки: нет подложки под курсором — это подпись.
//   3. Системного синего нет: заметная — Theme.brand, ссылка — Theme.accent.
//   4. Опасная тихая; залитая красная только в подтверждении, и там одна.
//   5. Кнопка без подписи обязана иметь .help и accessibilityLabel.
//   6. Долгое действие не скачет: та же ширина, кольцо, глагол в настоящем.
//   7. Недоступное выключено, а не спрятано.

enum CharoiteRole {
    /// Действие с последствием: запись, отправка, доступ. Одна на панель.
    case prominent
    /// Рабочая лошадь: открыть, проверить, экспортировать.
    case regular
    /// Обслуживает панель: подсказка, фильтр, повтор.
    case quiet
    /// Переход внутри приложения, внутри фразы. Ничего не меняет.
    case link
    /// Удаление. Тихая по умолчанию.
    case destructive
    /// Подтверждение удаления — единственная залитая красная в приложении.
    case destructiveFilled
    /// Только в шапке и только с .help. Квадрат по высоте размера.
    case icon
}

// MARK: - Размер
//
// Радиусы 8 и 12 из Theme описывают поверхности — карточки, поля, пузыри.
// Кнопка — не поверхность, а элемент управления: её радиус привязан к
// высоте, иначе кнопка 21 pt превращается в капсулу. Это осознанная
// поправка к docs/DESIGN.md, а не третий радиус «просто так».

enum CharoiteSize {
    /// 21 — в заголовке панели и в строке списка.
    case s
    /// 26 — размер по умолчанию.
    case m
    /// 32 — первый запуск, пустое состояние, подтверждение.
    case l

    var height: CGFloat {
        switch self {
        case .s: return 21
        case .m: return 26
        case .l: return 32
        }
    }

    var radius: CGFloat {
        switch self {
        case .s: return 5
        case .m: return 6
        case .l: return 7
        }
    }

    var fontSize: CGFloat {
        switch self {
        case .s: return 11.5
        case .m: return 12.5
        case .l: return 13.5
        }
    }

    /// Тихая кнопка уже заметной на 2 pt с каждой стороны: у неё нет рамки,
    /// и одинаковый отступ визуально делает её шире соседей.
    func padding(_ role: CharoiteRole) -> CGFloat {
        switch role {
        case .link: return 0
        case .icon: return 0
        case .quiet, .destructive: return height * 0.4
        default: return height * 0.5
        }
    }
}

// MARK: - Стиль

struct CharoiteButtonStyle: ButtonStyle {
    var role: CharoiteRole = .regular
    var size: CharoiteSize = .m
    /// Долгое действие: кольцо вместо иконки, нажатие не проходит.
    var isBusy: Bool = false

    func makeBody(configuration: Configuration) -> some View {
        Surface(configuration: configuration, role: role, size: size, isBusy: isBusy)
    }

    private struct Surface: View {
        let configuration: Configuration
        let role: CharoiteRole
        let size: CharoiteSize
        let isBusy: Bool

        @Environment(\.isEnabled) private var isEnabled
        @State private var isHovering = false

        private var isPressed: Bool { configuration.isPressed && !isBusy }
        private var isOff: Bool { !isEnabled || isBusy }

        var body: some View {
            HStack(spacing: size.height * 0.27) {
                if isBusy {
                    ProgressView()
                        .controlSize(.small)
                        .scaleEffect(size == .s ? 0.6 : 0.7)
                        .frame(width: size.fontSize, height: size.fontSize)
                        .tint(role == .prominent || role == .destructiveFilled ? .white : Theme.accent)
                }
                configuration.label
                    .font(.system(size: size.fontSize, weight: weight))
                    .lineLimit(1)
            }
            .foregroundStyle(foreground)
            .frame(height: size.height)
            .frame(minWidth: role == .icon ? size.height : nil)
            .padding(.horizontal, size.padding(role))
            .background(shape.fill(fill))
            .overlay(shape.strokeBorder(stroke))
            .shadow(color: shadow, radius: shadowRadius, y: shadowY)
            .underline(role == .link && isHovering && !isOff)
            .contentShape(shape)
            // Кнопка держит СВОЙ размер и не сжимается соседями: в тесном
            // ряду SwiftUI иначе ужимал подпись до «Стеногр…» и «Задач…».
            // Правило спеки: размер и положение кнопки не меняются никогда.
            .fixedSize(horizontal: true, vertical: false)
            .animation(.easeOut(duration: 0.12), value: isHovering)
            .animation(.easeOut(duration: 0.12), value: isPressed)
            .onHover { isHovering = $0 && !isOff }
        }

        private var shape: RoundedRectangle {
            RoundedRectangle(cornerRadius: role == .link ? 4 : size.radius, style: .continuous)
        }

        private var weight: Font.Weight {
            switch role {
            case .prominent, .destructiveFilled: return .semibold
            default: return .medium
            }
        }

        // MARK: Цвет текста

        private var foreground: Color {
            switch role {
            case .prominent, .destructiveFilled:
                return isOff ? Color.primary.opacity(0.34) : .white
            case .regular:
                return isOff ? Color.primary.opacity(0.3) : .primary
            case .quiet, .icon:
                if isOff { return Color.primary.opacity(0.3) }
                return isHovering || isPressed ? .primary : .secondary
            case .link:
                if isOff { return Theme.accent.opacity(0.4) }
                return isPressed ? Theme.accentDeep : (isHovering ? Theme.accentHover : Theme.accent)
            case .destructive:
                if isOff { return Theme.danger.opacity(0.38) }
                return isPressed ? Theme.dangerDeep : Theme.danger
            }
        }

        // MARK: Заливка

        private var fill: AnyShapeStyle {
            func s(_ style: some ShapeStyle) -> AnyShapeStyle { AnyShapeStyle(style) }

            switch role {
            case .prominent:
                if isOff { return s(Color.primary.opacity(0.09)) }
                if isPressed { return s(Theme.brandPressed) }
                return s(isHovering ? Theme.brandHover : Theme.brand)
            case .destructiveFilled:
                if isOff { return s(Color.primary.opacity(0.09)) }
                if isPressed { return s(Theme.dangerDeep) }
                return s(isHovering ? Theme.dangerHover : Theme.danger)
            case .regular:
                if isOff { return s(Color(nsColor: .controlBackgroundColor)) }
                if isPressed { return s(Color.primary.opacity(0.08)) }
                return s(isHovering
                         ? Color.primary.opacity(0.035)
                         : Color(nsColor: .controlBackgroundColor))
            case .quiet, .icon:
                if isOff { return s(Color.clear) }
                if isPressed { return s(Color.primary.opacity(0.12)) }
                return s(isHovering ? Color.primary.opacity(0.06) : Color.clear)
            case .destructive:
                if isOff { return s(Color.clear) }
                if isPressed { return s(Theme.danger.opacity(0.17)) }
                return s(isHovering ? Theme.danger.opacity(0.09) : Color.clear)
            case .link:
                return s(Color.clear)
            }
        }

        // MARK: Рамка

        private var stroke: Color {
            guard role == .regular else { return .clear }
            if isOff { return .primary.opacity(0.09) }
            return .primary.opacity(isHovering || isPressed ? 0.26 : 0.15)
        }

        // MARK: Тень
        //
        // Тень есть только у заметной кнопки и только пока она нажимаема:
        // это единственный элемент интерфейса, который поднят над панелью.

        private var shadow: Color {
            guard role == .prominent, !isOff, !isPressed else { return .clear }
            return Theme.accent.opacity(isHovering ? 0.5 : 0.4)
        }

        private var shadowRadius: CGFloat { isHovering ? 11 : 8 }
        private var shadowY: CGFloat { isHovering ? 5 : 3 }
    }
}

// MARK: - Токены, которых не хватало в Theme

extension Theme {
    /// Наведение и нажатие для фирменного градиента: тот же угол,
    /// светлее и темнее на один шаг.
    static let brandHover = LinearGradient(
        colors: [Color(hex: "#7376F5"), Color(hex: "#9A6DF8")],
        startPoint: .topLeading, endPoint: .bottomTrailing)

    static let brandPressed = LinearGradient(
        colors: [Color(hex: "#5457DE"), Color(hex: "#7B4CE3")],
        startPoint: .topLeading, endPoint: .bottomTrailing)

    static let accentHover = Color(hex: "#4F46E5")
    static let accentDeep = Color(hex: "#4338CA")

    /// Удаление — красный, а не оранжевый Theme.overdue: просрочка
    /// предупреждает, удаление уничтожает.
    static let danger = Color(hex: "#DC2626")
    static let dangerHover = Color(hex: "#E03B3B")
    static let dangerDeep = Color(hex: "#B91C1C")
}

// MARK: - Вызов

extension View {
    /// `Button("Открыть") { … }.charoite(.prominent)`
    ///
    /// Кольцо фокуса рисует система по contentShape — чтобы оно было
    /// индиго, а не системно-синим, в корне приложения должен стоять
    /// `.tint(Theme.accent)`.
    func charoite(_ role: CharoiteRole = .regular,
                  _ size: CharoiteSize = .m,
                  busy: Bool = false) -> some View {
        buttonStyle(CharoiteButtonStyle(role: role, size: size, isBusy: busy))
            .disabled(busy)
    }
}

private extension View {
    @ViewBuilder func underline(_ active: Bool) -> some View {
        if active {
            overlay(alignment: .bottom) {
                Rectangle().frame(height: 1).offset(y: -2)
            }
        } else {
            self
        }
    }
}

// MARK: - Сегмент обслуживающих действий
//
// Три тихие кнопки в шапке встречи («Подсказка», «Claude», «Протокол»)
// читались как три подписи. Сегмент показывает, что это одна группа
// одного веса — и что заметная кнопка справа к ней не относится.

struct CharoiteSegment<Content: View>: View {
    var size: CharoiteSize = .s
    @ViewBuilder var content: () -> Content

    var body: some View {
        HStack(spacing: 2) { content() }
            .padding(2)
            .background(RoundedRectangle(cornerRadius: size.radius + 2, style: .continuous)
                .fill(Color.primary.opacity(0.05)))
    }
}

// MARK: - Предпросмотр

#Preview("Шкала кнопок") {
    VStack(alignment: .leading, spacing: 18) {
        HStack(spacing: 9) {
            Button("Начать запись") {}.charoite(.prominent)
            Button("Стенограмма") {}.charoite()
            Button("Подсказка") {}.charoite(.quiet)
            Button("Все задачи") {}.charoite(.link)
            Button("Забыть встречу") {}.charoite(.destructive)
            Button { } label: { Image(systemName: "gearshape") }
                .charoite(.icon)
                .help("Настройки")
        }
        HStack(spacing: 9) {
            Button("Выключено") {}.charoite(.prominent).disabled(true)
            Button("Собираю") {}.charoite(busy: true)
            Button("Удалить безвозвратно") {}.charoite(.destructiveFilled, .l)
        }
        HStack(spacing: 9) {
            Button("S") {}.charoite(.prominent, .s)
            Button("M") {}.charoite(.prominent, .m)
            Button("L") {}.charoite(.prominent, .l)
            CharoiteSegment {
                Button("Подсказка") {}.charoite(.quiet, .s)
                Button("Claude") {}.charoite(.quiet, .s)
                Button("Протокол") {}.charoite(.quiet, .s)
            }
        }
    }
    .padding(26)
    .tint(Theme.accent)
}

#endif
