import Foundation

/// The latest question is mandatory. The budget limits older conversation,
/// not whether Ollama receives the question at all. It is a character heuristic,
/// not a guarantee that an arbitrarily large prompt fits a model's token limit.
enum ChatContextWindow {
    /// Предел самого вопроса в знаках. Окно модели — 32 768 токенов
    /// (`LocalChatService`): минус ответ (2 048), блок памяти (до 5 000 знаков)
    /// и системный промпт. Для CJK знак ≈ токен, поэтому предел считается по
    /// худшему случаю, а не по кириллице.
    static let maxPrompt = 20_000

    struct Entry: Equatable {
        let role: String
        let text: String
    }

    static func select(history: [Entry], prompt: String, budget: Int = 12_000) -> [Entry] {
        let latest = Entry(role: "user", text: prompt)
        var remaining = max(0, max(0, budget) - prompt.count)
        var selected: [[Entry]] = []
        var turns: [[Entry]] = []
        for entry in history where !entry.text.isEmpty {
            if entry.role == "user" {
                turns.append([entry])
            } else if entry.role == "assistant", !turns.isEmpty {
                turns[turns.count - 1].append(entry)
            }
        }
        // Keep whole recent turns: don't feed an orphan assistant answer.
        for turn in turns.reversed() {
            let cost = turn.reduce(0) { $0 + $1.text.count }
            guard cost <= remaining else { break }
            remaining -= cost
            selected.append(turn)
        }
        return selected.reversed().flatMap { $0 } + [latest]
    }
}
