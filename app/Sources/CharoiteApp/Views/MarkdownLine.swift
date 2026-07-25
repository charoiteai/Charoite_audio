import SwiftUI

/// Построчный inline-Markdown для ответов локальной модели.
///
/// Модели пишут **жирное**, `код` и заголовки — сырые звёздочки в баблах
/// выглядят как мусор. Полноценный Markdown-вью здесь избыточен (и ломает
/// подбор высоты в баблах), а inline-разметка построчно сохраняет переводы
/// строк и списки как есть.
enum MarkdownLine {
    /// Строка → AttributedString: inline-разметка, `## заголовок` → жирный.
    static func render(_ line: String) -> AttributedString {
        var work = line
        let trimmed = work.trimmingCharacters(in: .whitespaces)
        var forceBold = false
        if trimmed.hasPrefix("#") {
            work = trimmed.drop(while: { $0 == "#" }).trimmingCharacters(in: .whitespaces)
            forceBold = true
        }
        var piece = (try? AttributedString(
            markdown: work,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        )) ?? AttributedString(work)
        if forceBold { piece.font = .callout.bold() }
        return piece
    }

    /// Многострочный текст целиком (переводы строк сохраняются).
    static func render(text: String) -> AttributedString {
        var out = AttributedString()
        for (i, line) in text.components(separatedBy: "\n").enumerated() {
            if i > 0 { out.append(AttributedString("\n")) }
            out.append(render(String(line)))
        }
        return out
    }
}
