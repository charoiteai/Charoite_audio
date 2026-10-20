import Foundation

/// The latest question is mandatory. The budget limits older conversation,
/// not whether Ollama receives the question at all. The chat passes a token
/// budget and cost (`ChatRetrieval.historyWindow`); the default cost counts
/// characters.
enum ChatContextWindow {
    /// Предел самого вопроса в знаках — для строки статуса. Окно модели —
    /// 32 768 токенов (`ChatRetrieval.Window`): минус ответ, системный промпт
    /// и блок памяти, который `ChatRetrieval.fit` ужимает под остаток.
    static let maxPrompt = 20_000
    /// Тот же предел в токенах оценки: редкий иероглиф — 3,5 токена, и 20 000
    /// таких знаков не влезли бы в окно и без памяти (№743).
    static let maxPromptTokens = 20_000

    struct Entry: Equatable {
        let role: String
        let text: String
    }

    static func select(history: [Entry], prompt: String, budget: Int = 12_000,
                       cost: (String) -> Int = { $0.count }) -> [Entry] {
        let latest = Entry(role: "user", text: prompt)
        var remaining = max(0, max(0, budget) - cost(prompt))
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
            let spent = turn.reduce(0) { $0 + cost($1.text) }
            guard spent <= remaining else { break }
            remaining -= spent
            selected.append(turn)
        }
        return selected.reversed().flatMap { $0 } + [latest]
    }
}
