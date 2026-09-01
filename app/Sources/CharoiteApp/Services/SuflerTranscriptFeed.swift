// Коалессер живой ленты (№153, sample 01.09): каждый 3с-чанк транскрипта
// (и разметка, и переименование) публиковал отдельную пересборку ВСЕГО
// SuflerView с его пятью ObservedObject — вместе с 30 fps hint-стрима это
// давало 98 % main thread в flushTransactions и «кнопки не нажимаются».
// Мутации идут в тень _linesShadow, @Published lines обновляется пачкой
// не чаще раза в 250 мс; смена фазы записи флашит немедленно. Тот же
// класс, что №50 (секундные часы): частое — вон из общего объекта.
// Внешних читателей у lines нет (проверено rg) — тень читает только сервис.
import Foundation

extension SuflerService {
    /// Пачка тени → @Published lines, не чаще раза в 250 мс. Немедленный
    /// flushLines() — на смене жизненного цикла (стоп не ждёт таймер).
    func scheduleLinesFlush() {
        if _linesFlushScheduled { return }
        _linesFlushScheduled = true
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 250_000_000)
            self?.flushLines()
        }
    }

    func flushLines() {
        _linesFlushScheduled = false
        if lines != _linesShadow { lines = _linesShadow }
    }

    /// Тестовый вход в consume: №153 сломал бы молча — публикация ленты
    /// в обход коалессера не ловится тестами чистых функций.
    func consumeForTest(_ json: String) { consume(Data((json + "\n").utf8)) }
}
