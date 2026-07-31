package ai.charoite.companion

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Разбор графа. Форматы общие с Mac и Obsidian: ошибка здесь означает
 * галку, вставшую не в ту строку чужого файла.
 */
class GraphTextTest {

    @Test
    fun `тема встречи берётся после длинного тире`() {
        val md = "---\ntype: meeting\n---\n\n# 2026-07-27 — Бюджет и сроки\n\nтекст"
        assertEquals("Бюджет и сроки", GraphText.title(md, "запасное"))
    }

    @Test
    fun `заголовок без тире отдаётся целиком`() {
        assertEquals("Планёрка", GraphText.title("# Планёрка\n", "запасное"))
    }

    @Test
    fun `без заголовка остаётся имя файла`() {
        assertEquals("2026-07-27_1534", GraphText.title("просто текст", "2026-07-27_1534"))
    }

    @Test
    fun `штамп из имени файла`() {
        assertEquals("27.07 15:34", GraphText.stamp("2026-07-27_1534"))
        assertEquals("чужое-имя", GraphText.stamp("чужое-имя"))
    }

    @Test
    fun `распознаются оба маркера списка`() {
        assertTrue(GraphText.isTodo("- [ ] позвонить"))
        assertTrue(GraphText.isTodo("* [x] отправлено"))
        assertTrue(GraphText.isTodo("  - [X] с отступом"))
        assertFalse(GraphText.isTodo("- обычный пункт"))
        assertFalse(GraphText.isTodo("текст [ ] не список"))
    }

    @Test
    fun `текст задачи без маркера`() {
        assertEquals("позвонить Ивану", GraphText.todoText("- [ ] позвонить Ивану"))
        assertNull(GraphText.todoText("- просто пункт"))
    }

    @Test
    fun `переключение меняет ровно один символ`() {
        val md = "# Задачи\n- [ ] первая\n- [x] вторая\n"
        val out = GraphText.toggle(md, "первая", 1)
        assertEquals("# Задачи\n- [x] первая\n- [x] вторая\n", out)
    }

    @Test
    fun `снятие галки возвращает пробел`() {
        val md = "- [x] сделано\n"
        assertEquals("- [ ] сделано\n", GraphText.toggle(md, "сделано", 0))
    }

    @Test
    fun `строку ищем по тексту, а не по номеру`() {
        // Между сканом и тапом файл дописали сверху — подсказка устарела.
        val md = "# Новое сверху\n- [ ] свежая\n- [ ] нужная\n"
        val out = GraphText.toggle(md, "нужная", 1)
        assertEquals("# Новое сверху\n- [ ] свежая\n- [x] нужная\n", out)
    }

    @Test
    fun `исчезнувшая задача не трогает файл`() {
        val md = "- [ ] первая\n"
        assertNull(GraphText.toggle(md, "которой нет", 0))
    }

    @Test
    fun `совпадение в обычной строке не считается задачей`() {
        val md = "Обсудили: позвонить Ивану\n- [ ] позвонить Ивану\n"
        val out = GraphText.toggle(md, "позвонить Ивану", 5)
        assertEquals("Обсудили: позвонить Ивану\n- [x] позвонить Ивану\n", out)
    }
}
