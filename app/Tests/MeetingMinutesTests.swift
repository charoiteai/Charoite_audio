import XCTest
@testable import CharoiteApp

/// Разбор Минуток. Разметку пишет модель, поэтому в архиве живут сразу три
/// стиля — тесты фиксируют, что карточка понимает каждый.
final class MeetingMinutesTests: XCTestCase {
    func testДефисныйФорматСВложенностью() {
        // Стиль встречи 2026-08-05 13-34.
        let minutes = MeetingMinutes.parse("""
        <!-- черновик, встреча идёт -->
        - Участники: Мария, Игорь, Пётр.
        - Темы:
          - Архитектура взаимодействия плагина и промежуточного звена.
          - Развертывание и тестирование.
        - Решения:
          - Команда берёт разработку ядра на себя.
        - Поручения:
          - **Мария** — развернуть локально; срок: немедленно.
        - Открытые вопросы:
          - Доступы к системам в целевом контуре.
        """)
        XCTAssertEqual(minutes.topics.map(\.text), [
            "Архитектура взаимодействия плагина и промежуточного звена.",
            "Развертывание и тестирование.",
        ])
        XCTAssertEqual(minutes.decisions.map(\.text), ["Команда берёт разработку ядра на себя."])
        XCTAssertEqual(minutes.tasks.map(\.text), ["Мария — развернуть локально; срок: немедленно."])
        XCTAssertEqual(minutes.openQuestions.count, 1)
        XCTAssertTrue(minutes.isDraft, "пометку черновика обязаны показать человеку")
    }

    func testУчастникиНеПопадаютВТемы() {
        // Раздел «Участники» распознан и отброшен: иначе список людей
        // оказывался первым пунктом тем.
        let minutes = MeetingMinutes.parse("""
        - Участники: Мария, Игорь, Ольга.
        - Темы:
          - Расчёт ставок.
        """)
        XCTAssertEqual(minutes.topics.map(\.text), ["Расчёт ставок."])
    }

    func testЖирныйЗаголовокИНумерацияСоЗвёздочками() {
        // Стиль встречи 2026-08-05 12-11: **Темы:** + «1.» + вложенные «*».
        let minutes = MeetingMinutes.parse("""
        **Участники:**
        *   Собеседник 1 (руководитель команды)

        **Темы:**
        1.  **Расчет стоимости команды и ставок:**
            *   Проблема: сумма считается по старой ставке.
        **Решения:**
        *   Утверждены новые ставки.
        """)
        XCTAssertEqual(minutes.topics.first?.text, "Расчет стоимости команды и ставок:")
        XCTAssertEqual(minutes.topics.first?.level, 0)
        XCTAssertEqual(minutes.topics.last?.text, "Проблема: сумма считается по старой ставке.")
        XCTAssertGreaterThan(minutes.topics.last?.level ?? 0, 0, "подпункт рисуется отступом")
        XCTAssertEqual(minutes.decisions.map(\.text), ["Утверждены новые ставки."])
    }

    func testШаблонRetroFillСЗаголовкамиИПустымиРазделами() {
        // Так пишет retro_fill: «## Темы» и «нет» вместо пустого раздела.
        let minutes = MeetingMinutes.parse("""
        # Минутки встречи
        **Дата/время:** 4 августа **Участники:** Мария
        ## Темы
        - Отопление корпуса.
        ## Решения
        нет
        ## Поручения
        - [ ] **Игорь** — заменить фильтры — до пятницы
        ## Риски
        - Труба на втором этаже.
        """)
        XCTAssertEqual(minutes.topics.map(\.text), ["Отопление корпуса."])
        XCTAssertTrue(minutes.decisions.isEmpty, "«нет» — это пустой раздел, а не пункт")
        XCTAssertEqual(minutes.tasks.map(\.text), ["Игорь — заменить фильтры — до пятницы"])
        XCTAssertEqual(minutes.risks.count, 1)
        XCTAssertFalse(minutes.isDraft)
    }

    func testДвоеточиеВнутриПунктаНеСчитаетсяЗаголовком() {
        let minutes = MeetingMinutes.parse("""
        - Решения:
          - Развернуть на Кубере: локальный запуск в Anaconda не годится.
        """)
        XCTAssertEqual(minutes.decisions.count, 1,
                       "длинная фраза с двоеточием — пункт, а не новый раздел")
    }

    func testЗаголовокСХвостомИЭмодзи() {
        // Живой архив: «## Темы обсуждения» и «### 👥 Участники:» —
        // на них парсер молчал и карточка оставалась короткой.
        let minutes = MeetingMinutes.parse("""
        ## Черновик минуток встречи
        ### 👥 Участники:
        *   Собеседник 2
        ## Темы обсуждения
        1.  **Сравнение подходов к автоматизации:**
        ## Решения и договорённости
        *   Берём вариант А.
        """)
        XCTAssertEqual(minutes.topics.map(\.text), ["Сравнение подходов к автоматизации:"])
        XCTAssertEqual(minutes.decisions.map(\.text), ["Берём вариант А."])
        XCTAssertFalse(minutes.topics.contains { $0.text == "Собеседник 2" },
                       "участники не должны утекать в темы")
    }

    func testПунктСДвоеточиемНеСтановитсяЗаголовком() {
        // «Решения по бюджету: …» начинается с ключевого слова, но это пункт:
        // раздел с хвостом текста в той же строке заголовком не признаём.
        let minutes = MeetingMinutes.parse("""
        - Темы:
          - Решения по бюджету: снизить расходы на 5%.
          - Сроки поставки.
        """)
        XCTAssertEqual(minutes.topics.count, 2)
        XCTAssertTrue(minutes.decisions.isEmpty)
    }

    func testПустойФайлДаётПустойПротокол() {
        XCTAssertTrue(MeetingMinutes.parse("").isEmpty)
        XCTAssertTrue(MeetingMinutes.parse("<!-- черновик -->\n\n").isEmpty,
                      "один комментарий — не повод показывать переключатель")
    }
}
