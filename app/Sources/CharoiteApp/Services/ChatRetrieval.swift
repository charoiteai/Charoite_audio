import Foundation

/// Сборка хода чата с памятью: запрос к поиску, параметры поиска, основа
/// системного промпта, блок графа и тело запроса к Ollama (№632).
///
/// До этого всё жило внутри `LocalChatService.stream`, а пробы и бенч памяти
/// собирали свой ход сами: 5×1200 вместо 8×800, свой промпт, без бюджета и
/// без правила соседей по [[ссылке]]. Бенч мерил не тот путь, которым отвечает
/// чат. Здесь — одно место для чата и проб; сеть и состояние экрана остаются
/// у `LocalChatService`.
enum ChatRetrieval {
    /// Параметры поиска для блока графа.
    struct Params: Equatable {
        let limit: Int
        let snippet: Int
        let budget: Int

        /// Как у чата: бюджет задаётся поиску, а не срезается по хвосту —
        /// prefix(5000) резал последний источник на полуслове.
        static let chat = Params(limit: 8, snippet: 800, budget: 5000)
    }

    /// Блок графа для системного промпта и что из него нужно экрану.
    struct Context: Equatable {
        /// Выдача поиска без маркера слабых совпадений.
        let text: String
        /// Совпадения слабые: модель предупреждена, чипов нет.
        let weak: Bool
        /// Готовый блок для системного промпта; пусто — подмешивать нечего.
        let block: String
    }

    /// Запрос к поиску из разговора. Поиск по одной последней реплике
    /// («а что дальше?») находил мусор: тему несут ПОСЛЕДНИЕ вопросы вместе,
    /// свежий — главный.
    static func query(from messages: [LocalChatService.Message]) -> String {
        String((messages.filter { $0.role == "user" }.suffix(3).map(\.text)
            .joined(separator: " ") as String).suffix(500))
    }

    /// Основа системного промпта до блока графа. Промпт локализован целиком:
    /// при sufler.language: en интерфейс и поиск отвечали по-английски, а
    /// чат — по-русски, потому что этого требовала зашитая строка.
    static func baseSystem(inferenceIsLocal: Bool) -> String {
        var system = L.t("""
        Ты — Чароит, ассистент владельца этого Mac. \
        Отвечай по-русски, кратко и по делу. ЧЕСТНОСТЬ: отвечай только \
        по данным из блоков ниже и из разговора; если блоки не про то, что спросили, — \
        так и скажи («в памяти этого нет»), не сочиняй. Ты помнишь весь текущий \
        диалог — «а что я спрашивал», «продолжи», «а по второму пункту» относятся \
        к нему. Сопоставляй факты из разных встреч: называй повторяющиеся темы, \
        расхождения и что изменилось со временем, с датами и источниками из блока \
        графа. На вопрос «кто ты» — «Чароит, ассистент», без имени \
        вендора модели.
        """, """
        You are Charoite, the assistant of this Mac's owner. \
        Answer in English, briefly and to the point. HONESTY: answer only from the \
        blocks below and from this conversation; if the blocks are not about what \
        was asked, say so ("that is not in memory") and do not invent. You remember \
        the whole current dialogue — "what did I ask", "go on", "what about the \
        second point" refer to it. Cross-reference facts across meetings: name \
        recurring topics, contradictions and what changed over time, with dates and \
        sources from the graph block. To "who are you" — "Charoite, an \
        assistant", without naming the model vendor.
        """, """
        你是 Charoite，这台 Mac 主人的助手。用中文简洁地回答。\
        诚实原则：只依据下面的资料块和当前对话作答；如果资料块与所问无关，\
        就直说「记忆中没有这条」，不要编造。你记得整段对话——「我刚才问了什么」\
        「继续」「第二点呢」都指向它。跨会议比对事实：指出反复出现的主题、\
        分歧以及随时间发生的变化，并附上图谱资料块中的日期与来源。\
        被问「你是谁」时回答「Charoite，助手」，不要提模型厂商。
        """)
        system += inferenceIsLocal
            ? L.t("\nМодель отвечает на этом Mac.", "\nThe model responds on this Mac.", "\n模型在此 Mac 上生成回复。")
            : L.t("\nМодель отвечает на другом сервере, явно выбранном в настройках; не называй этот ответ офлайн или локальным.",
                  "\nThe model responds on another server explicitly chosen in settings; do not call this response offline or local.",
                  "\n模型在设置中明确选择的其他服务器上生成回复；请勿称此回复为离线或本地。")
        return system
    }

    /// Блок графа из выдачи поиска. Маркер слабых совпадений
    /// (`ArchiveSearch.lowConfidenceMarker`) снимается здесь и только здесь:
    /// модель предупреждена, что граф скорее не про это.
    static func contextBlock(vault raw: String) -> Context {
        var vault = raw
        let weak = vault.hasPrefix(ArchiveSearch.lowConfidenceMarker)
        if weak { vault.removeFirst() }
        guard !vault.isEmpty else { return Context(text: vault, weak: weak, block: "") }
        let head = weak
            ? "\n\n[ГРАФ ВСТРЕЧ — совпадения СЛАБЫЕ, вероятно не про вопрос; не выдавай за факт]\n"
            : "\n\n[ГРАФ ВСТРЕЧ — люди, системы, решения, документация]\n"
        return Context(text: vault, weak: weak, block: head + vault)
    }

    /// Тело запроса `/api/chat`. `stream: false` и температура 0 — для проб:
    /// бенч сравнивает правки, а не разброс генерации.
    static func requestBody(model: String, messages: [[String: String]],
                            stream: Bool, temperature: Double = 0.35) -> [String: Any] {
        [
            "model": model,
            "messages": messages,
            "stream": stream,
            "think": false,           // критично: дефолтный thinking = ~10с молчания
            "keep_alive": "30m",
            // num_ctx как в OllamaService: без него Modelfile-дефолт 262144 →
            // перезагрузка 23GB модели при переключении чатов и пустой 2-й ответ.
            // 32768 — потолок для 32 ГБ машины (KV-кэш ~3-5 ГБ при 23 ГБ модели):
            // система+граф (5К) + история (12К) + ответ дышат свободно. 64К на
            // 32 ГБ уходит в своп — не поднимать без 64 ГБ RAM.
            "options": ["temperature": temperature, "num_ctx": 32768, "num_predict": 2048],
        ]
    }
}
