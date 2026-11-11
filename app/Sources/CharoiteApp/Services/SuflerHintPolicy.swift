#if os(macOS)
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

extension SuflerService {
    // Ручные запросы карточки — рядом с её политикой (вынос №153:
    // сервис у потолка file_length, связные блоки переезжают по смыслу).
    // Близнецы различались одной строкой команды — правка дедлайна
    // поехала бы только в одного (GLM r1 по #473).
    func requestHint() { requestCard("hint") }
    func requestSummary() { requestCard("summary") }

    nonisolated static func utf8ClampedQuestion(_ raw: String, maxBytes: Int = 8_000) -> String {
        guard raw.utf8.count > maxBytes else { return raw }
        var out = String.UnicodeScalarView()
        var used = 0
        for scalar in raw.unicodeScalars {
            let scalarBytes = String(scalar).utf8.count
            if used + scalarBytes > maxBytes { break }
            out.append(scalar)
            used += scalarBytes
        }
        return String(out).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    func resetManualCorrelation() {
        hintManualID = nil
        pendingManualQuestions = 0
        ignoredManualIDs.removeAll()
    }

    func ask(_ question: String) {
        let normalized = question.trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "\n", with: " ")
            .replacingOccurrences(of: "\0", with: "")
        let q = Self.utf8ClampedQuestion(normalized)
        // В классической карточке токены двух стримов смешались бы без manual_id;
        // в liveFeed новый вопрос вытесняет предыдущий по поколению manual_id.
        guard isRunning, !isHinting || liveFeed, !q.isEmpty else { return }
        if let prev = hintManualID {
            ignoredManualIDs.insert(prev)
        }
        hintManualID = nil
        if liveFeed {
            pendingManualQuestions += 1
        }
        manualRequestEpoch = UUID()
        hint = ""
        _hintBuf = ""; _lastHintUI = .distantPast
        isHinting = true
        hintIsManual = true
        // тот же предохранитель, что у «Подсказки»/«Протокола»: без него зависший
        // ответ держал поле «Спросить» и кнопки отключёнными до конца встречи
        armHintTimeout()
        send("ask " + q)
    }

    func acceptManualQuestionFrame(_ obj: [String: Any]) {
        guard (obj["state"] as? String) == "question",
              let incomingID = obj["manual_id"] as? String, !incomingID.isEmpty else { return }
        if pendingManualQuestions > 0 {
            pendingManualQuestions -= 1
            if pendingManualQuestions > 0 || !isHinting {
                ignoredManualIDs.insert(incomingID)
                return
            }
        } else if !isHinting {
            return
        }
        if let prev = hintManualID, prev != incomingID {
            ignoredManualIDs.insert(prev)
        }
        hintManualID = incomingID
    }

    /// Сопоставляет кадр hint / hint_done с текущим ручным запросом по manual_id.
    /// Возвращает nil, если пришёл чужой или вытесненный manual_id поверх активного вопроса.
    func bindManualStreamFrame(_ obj: [String: Any]) -> Bool? {
        let manual = obj["manual"] as? Bool ?? isHinting
        if manual, (obj["rejected"] as? Bool) == true {
            if let incomingID = obj["manual_id"] as? String, !incomingID.isEmpty {
                let alreadyAccounted = ignoredManualIDs.contains(incomingID) || hintManualID == incomingID
                ignoredManualIDs.insert(incomingID)
                if !alreadyAccounted, pendingManualQuestions > 0 {
                    pendingManualQuestions -= 1
                }
            } else if pendingManualQuestions > 0 {
                pendingManualQuestions -= 1
            }
            if pendingManualQuestions > 0 {
                return nil
            }
            hintManualID = nil
            return true
        }
        if manual {
            if let incomingID = obj["manual_id"] as? String {
                if ignoredManualIDs.contains(incomingID) || pendingManualQuestions > 0 {
                    return nil
                }
                if isHinting, let currentID = hintManualID, currentID != incomingID {
                    return nil
                }
                hintManualID = incomingID
            } else if pendingManualQuestions > 0 {
                return nil
            } else {
                hintManualID = nil
            }
        } else {
            hintManualID = nil
        }
        return manual
    }

    private func requestCard(_ cmd: String) {
        guard isRunning, !isHinting else { return }
        manualRequestEpoch = UUID()
        hint = ""
        _hintBuf = ""; _lastHintUI = .distantPast
        isHinting = true
        hintIsManual = true
        armHintTimeout()
        send(cmd)
    }
}
#endif
