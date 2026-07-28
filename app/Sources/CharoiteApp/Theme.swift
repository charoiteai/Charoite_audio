import SwiftUI

/// Дизайн-токены Charoite — те же, что в iOS-приложении (app-ios/Theme.swift).
/// Один источник характера на обеих платформах: индиго #6366F1 — действие,
/// чароит-фиолет #8B5CF6 — характер, растяжка между ними — фирменный градиент.
enum Theme {
    static let accent = Color(hex: "#6366F1")
    static let violet = Color(hex: "#8B5CF6")
    static let sky = Color(hex: "#0EA5E9")     // облачная лента Claude
    static let ok = Color(hex: "#059669")

    /// Градиент «живого» действия: запись, первый запуск, акцентные кнопки.
    static let brand = LinearGradient(
        colors: [accent, violet], startPoint: .topLeading, endPoint: .bottomTrailing)

    /// Геометрия (docs/DESIGN.md): 8 — поля и мелкие карточки, 12 — крупные
    /// карточки и пузыри. Третьего радиуса нет — капсулы делает Capsule().
    static let radius: CGFloat = 8
    static let radiusCard: CGFloat = 12
}
