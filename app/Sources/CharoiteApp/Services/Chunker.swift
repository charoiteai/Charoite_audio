import Foundation

/// Нарезка markdown на блоки для семантического индекса.
///
/// Зачем вообще. Раньше на файл строился ОДИН вектор по первым 12 000 знакам
/// («суть узла — в начале файла»). Для узла человека или ядра это правда, для
/// стенограммы встречи — ровно наоборот: решения принимают в конце. Замер по
/// рабочему графу: 325 файлов из 1172 длиннее этой границы, и 63% всего
/// содержимого в семантический индекс не попадало вообще. Вопрос «что решили»
/// не находился семантикой именно потому, что вектор построен по «все
/// собрались, слышно меня?».
///
/// Отдельно: Ollama молча режет вход bge-m3 примерно на 12 300 знаках, хотя
/// модель заявлена на 8192 токена. Проверено — добавленный в конец уникальный
/// маркер не меняет вектор (косинус 0.999999). Ошибки при этом никакой, так
/// что дефект невидим. Блоки заведомо короче предела, поэтому обрезка нас
/// больше не касается.
///
/// Границы — по заголовкам markdown: структура в графе честная, и она уже
/// бесплатно даёт то, ради чего в других проектах считают семантический
/// чанкинг (признанный в 2025 не окупающимся). Слишком длинная секция режется
/// по абзацам с перекрытием.
enum Chunker {
    /// Целевой размер блока в знаках (~450 токенов русского).
    static let target = 1800
    /// Перекрытие между блоками одной секции: мысль на стыке не должна пропасть.
    static let overlap = 200
    /// Короче этого блок не самостоятелен — приклеиваем к соседу.
    static let minChunk = 200

    struct Chunk: Equatable {
        /// Путь заголовков: «Файл → H1 → H2». Дописывается в текст перед
        /// эмбеддингом: без него блок «ну да, давайте так и сделаем»
        /// бесполезен и для вектора, и для человека в выдаче.
        let breadcrumb: String
        /// Текст блока как он есть в файле (для показа в выдаче).
        let text: String
        /// Смещение в исходном файле — чтобы сниппет можно было расширить.
        let offset: Int

        /// То, что реально уходит в эмбеддер.
        var embeddingText: String { breadcrumb.isEmpty ? text : "\(breadcrumb)\n\n\(text)" }
    }

    /// Разбить документ. `title` — имя файла без расширения, голова хлебной крошки.
    static func chunks(of text: String, title: String) -> [Chunk] {
        let sections = splitByHeadings(text, title: title)
        var out: [Chunk] = []
        // Короткие секции не выбрасываем, а копим: у узлов графа (Люди,
        // Системы, Ядра) секции по 100-300 знаков — «## Статус», «## Связи», —
        // и каждая из них ценна. Отбрасывать их значило бы выкинуть из
        // индекса самые аккуратные, руками написанные части графа.
        var pending: [Section] = []

        func flushPending() {
            guard !pending.isEmpty else { return }
            let body = pending.map(\.body).joined(separator: "\n\n")
            out.append(Chunk(breadcrumb: pending[0].crumb, text: body, offset: pending[0].offset))
            pending = []
        }

        for section in sections {
            if section.body.count > target {
                flushPending()
                out += split(section: section)
                continue
            }
            pending.append(section)
            let size = pending.reduce(0) { $0 + $1.body.count + 2 }
            if size >= target - overlap { flushPending() }
        }
        flushPending()
        // Пустой документ или один короткий абзац — отдаём как есть, иначе
        // файл выпадет из индекса совсем.
        if out.isEmpty {
            let body = text.trimmingCharacters(in: .whitespacesAndNewlines)
            if !body.isEmpty {
                out.append(Chunk(breadcrumb: title, text: String(body.prefix(target)), offset: 0))
            }
        }
        return out
    }

    // MARK: - Внутреннее

    private struct Section {
        let crumb: String
        let body: String
        let offset: Int
    }

    /// Режем по заголовкам, ведя стек уровней для хлебной крошки.
    private static func splitByHeadings(_ text: String, title: String) -> [Section] {
        var sections: [Section] = []
        var stack: [(level: Int, name: String)] = []
        var buffer = ""
        var bufferStart = 0
        var offset = 0
        var inFence = false

        func flush(_ crumbStack: [(level: Int, name: String)]) {
            let body = buffer.trimmingCharacters(in: .whitespacesAndNewlines)
            buffer = ""
            guard !body.isEmpty else { return }
            let crumb = ([title] + crumbStack.map(\.name)).joined(separator: " → ")
            sections.append(Section(crumb: crumb, body: body, offset: bufferStart))
        }

        for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
            let raw = String(line)
            // Заголовок внутри блока кода — не заголовок.
            if raw.hasPrefix("```") || raw.hasPrefix("~~~") { inFence.toggle() }
            let heading = inFence ? nil : headingLevel(raw)
            if let (level, name) = heading {
                flush(stack)
                while let last = stack.last, last.level >= level { stack.removeLast() }
                stack.append((level, name))
                bufferStart = offset
            }
            buffer += raw + "\n"
            offset += raw.count + 1
        }
        flush(stack)
        return sections
    }

    private static func headingLevel(_ line: String) -> (Int, String)? {
        guard line.hasPrefix("#") else { return nil }
        let hashes = line.prefix(while: { $0 == "#" }).count
        guard hashes <= 6, line.dropFirst(hashes).hasPrefix(" ") else { return nil }
        let name = line.dropFirst(hashes + 1).trimmingCharacters(in: .whitespaces)
        return name.isEmpty ? nil : (hashes, name)
    }

    /// Длинная секция → блоки по абзацам с перекрытием. Абзац целиком длиннее
    /// цели (сплошная стенограмма без пустых строк) режется по предложениям.
    private static func split(section: Section) -> [Chunk] {
        var out: [Chunk] = []
        var current = ""
        var currentStart = section.offset
        var cursor = section.offset

        func emit() {
            let body = current.trimmingCharacters(in: .whitespacesAndNewlines)
            current = ""
            guard body.count >= minChunk else { return }
            out.append(Chunk(breadcrumb: section.crumb, text: body, offset: currentStart))
        }

        for para in section.body.components(separatedBy: "\n\n") {
            let piece = para + "\n\n"
            if current.count + piece.count > target, !current.isEmpty {
                emit()
                // Хвост предыдущего блока — начало следующего: реплика на
                // стыке абзацев не должна потеряться между блоками.
                current = String(current.suffix(overlap))
                currentStart = max(section.offset, cursor - overlap)
            }
            if piece.count > target {
                for sentence in splitSentences(piece) {
                    if current.count + sentence.count > target, !current.isEmpty {
                        emit()
                        current = String(current.suffix(overlap))
                        currentStart = max(section.offset, cursor - overlap)
                    }
                    current += sentence
                }
            } else {
                current += piece
            }
            cursor += piece.count
        }
        emit()
        return out
    }

    /// Дробим по границам предложений, а если их нет — по длине.
    ///
    /// Без второго предохранителя сплошной текст без знаков препинания
    /// (сырая расшифровка, лог, выгрузка) возвращался одним куском на
    /// десятки тысяч знаков — и молча обрезался эмбеддером до первых
    /// нескольких процентов, то есть попадал в индекс почти пустым.
    private static func splitSentences(_ text: String) -> [String] {
        var out: [String] = []
        var cur = ""
        for ch in text {
            cur.append(ch)
            if ".!?…".contains(ch) || cur.count >= target {
                out.append(cur)
                cur = ""
            }
        }
        if !cur.isEmpty { out.append(cur) }
        return out
    }
}
