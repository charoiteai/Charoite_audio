import SwiftUI

/// Дизайн-токены Charoite — единые для iOS и (постепенно) macOS.
/// Палитра от камня: индиго #6366F1 (акцент действия) + чароит-фиолет
/// #8B5CF6 (характер). Тёмная тема — фиолетово-угольная, не серая.
enum Theme {
    static let accent = Color(red: 0.39, green: 0.40, blue: 0.95)   // #6366F1
    static let violet = Color(red: 0.55, green: 0.36, blue: 0.96)   // #8B5CF6
    static let sky = Color(red: 0.05, green: 0.65, blue: 0.91)      // #0EA5E9 — облачное
    static let ok = Color(red: 0.02, green: 0.59, blue: 0.41)       // #059669

    /// Геометрия (docs/DESIGN.md): 8 — поля и мелкие карточки, 12 — крупные.
    static let radius: CGFloat = 8
    static let radiusCard: CGFloat = 12

    /// Градиент кнопки записи — из мокапа 27.07.
    static let record = RadialGradient(
        colors: [violet, accent],
        center: .init(x: 0.32, y: 0.28), startRadius: 8, endRadius: 120)

    static func label(_ text: String) -> some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .textCase(.uppercase)
            .kerning(0.8)
            .foregroundStyle(.secondary)
    }
}
