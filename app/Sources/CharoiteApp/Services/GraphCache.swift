import Foundation

/// Кэш прочитанных файлов графа: содержимое и его нормализованная копия.
///
/// Поиск читал ВЕСЬ граф с диска на каждый запрос и заново приводил его к
/// нижнему регистру. На рабочем графе (1172 файла, 10 МБ) это 1.8-2.5 секунды
/// на вопрос — и в чате с памятью поиск идёт на КАЖДОЕ сообщение, то есть
/// диалог из десяти реплик перечитывал граф десять раз. При iCloud-хранении
/// это ещё и десять волн обращений к File Provider.
///
/// Кэш инвалидируется по mtime: правка в Obsidian видна сразу, а
/// неизменившиеся файлы не читаются повторно. Объём ограничен — на большом
/// графе кэш не должен вытеснять всё остальное из памяти; при переполнении
/// выбрасываются файлы, к которым дольше всего не обращались.
actor GraphCache {
    static let shared = GraphCache()

    struct Entry {
        let text: String
        let low: String        // нормализованная копия (для сниппетов)
        /// UTF-8 байты нормализованной копии.
        ///
        /// Поиск игл по String.range(of:) стоит дорого: каждый шаг сравнивает
        /// строки с юникод-нормализацией, то есть учитывает эквивалентность
        /// составных символов. Нам это не нужно — обе стороны уже приведены к
        /// нижнему регистру и «ё»→«е». По байтам то же самое считается в разы
        /// быстрее, а память та же (кириллица в UTF-8 — два байта).
        let bytes: [UInt8]
        let mtime: Double
    }

    private struct Slot {
        let entry: Entry
        var touched: UInt64    // счётчик обращений — дешёвый LRU
    }

    private var slots: [String: Slot] = [:]
    private var clock: UInt64 = 0
    private var bytes = 0

    /// Потолок кэша. 64 МБ текста — это порядка 6000 файлов рабочего размера;
    /// больше держать смысла нет, а меньше — начинается перечитывание.
    private static let maxBytes = 64 * 1024 * 1024

    /// Содержимое файла: из кэша, если он не менялся, иначе с диска.
    /// `normalize` вызывается только при реальном чтении.
    func text(at url: URL, key: String, mtime: Double,
              normalize: (String) -> String) -> Entry? {
        clock &+= 1
        if var slot = slots[key], slot.entry.mtime == mtime {
            slot.touched = clock
            slots[key] = slot
            return slot.entry
        }
        guard let text = try? String(contentsOf: url, encoding: .utf8) else {
            drop(key)
            return nil
        }
        let low = normalize(text)
        let entry = Entry(text: text, low: low, bytes: Array(low.utf8), mtime: mtime)
        put(key, entry)
        return entry
    }

    /// Забыть файлы, которых больше нет в графе (переименование, удаление).
    func retain(keys: Set<String>) {
        for key in slots.keys where !keys.contains(key) { drop(key) }
    }

    func clear() {
        slots = [:]
        bytes = 0
    }

    var count: Int { slots.count }

    private func put(_ key: String, _ entry: Entry) {
        drop(key)
        slots[key] = Slot(entry: entry, touched: clock)
        bytes += entry.text.utf8.count + entry.low.utf8.count * 2
        evictIfNeeded()
    }

    private func drop(_ key: String) {
        guard let old = slots.removeValue(forKey: key) else { return }
        bytes -= old.entry.text.utf8.count + old.entry.low.utf8.count * 2
    }

    private func evictIfNeeded() {
        guard bytes > Self.maxBytes else { return }
        // Выбрасываем самые давние, пока не уложимся в четыре пятых потолка:
        // выселять ровно до границы значит выселять на каждом следующем файле.
        let order = slots.sorted { $0.value.touched < $1.value.touched }
        for (key, _) in order {
            drop(key)
            if bytes <= Self.maxBytes * 4 / 5 { break }
        }
    }
}
