// Политика карточки подсказки: когда обновление нити вправе её погасить.
// Отдельный файл вместо остаточного места в соседних (GLM r1 по #472):
// SuflerService трижды упирался в потолок 1000 строк, и связные блоки
// переезжают сюда по смыслу, а не туда, где осталось место.
import Foundation
extension SuflerService {
    /// Гасит ли обновление нити карточку подсказки. Чистая функция — обе
    /// критические ошибки ревью 16.08 (стирание ручного ответа, ампутация
    /// идущего авто-стрима) прошли бы мимо тестов, живи решение в consume.
    /// Гаснет только авто-контент, отгоревший своё: свежая авто-подсказка живёт
    /// минимум hintCardLifetime — карточка пропадала со следующим обновлением нити, за
    /// полминуты не читалась (владелец, 01.09). Стримы/ручной ответ нить не трогает;
    /// новая подсказка сменяет сразу, крестик работает всегда — старьё не копится.
    nonisolated static let hintCardLifetime: TimeInterval = 180
    nonisolated static func threadClearsHint(isHinting: Bool, isAutoHinting: Bool,
                                             hintIsManual: Bool,
                                             ageSeconds: TimeInterval = .infinity) -> Bool {
        !isHinting && !isAutoHinting && !hintIsManual
            && ageSeconds >= hintCardLifetime
    }
}
