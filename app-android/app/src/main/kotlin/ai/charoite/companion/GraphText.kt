package ai.charoite.companion

/**
 * Разбор markdown графа — без единого обращения к Android.
 *
 * Форматы те же, что читают Mac, Obsidian и iPhone-компаньон: файлы и есть
 * истина, своей базы нет. Держим логику отдельно от файловых обёрток —
 * тогда её можно проверять тестами, а не «на планшете».
 */
object GraphText {

    private val TODO = Regex("""^\s*[-*] \[( |x|X)] +(.+)$""")

    /** Тема встречи: первый `# …`, после « — » (иначе весь заголовок). */
    fun title(text: String, fallback: String): String {
        for (line in text.lineSequence()) {
            if (!line.startsWith("# ")) continue
            val head = line.removePrefix("# ")
            val sep = head.indexOf(" — ")
            return if (sep >= 0) head.substring(sep + 3) else head
        }
        return fallback
    }

    /** «2026-07-27_1534» → «27.07 15:34»; чужое имя возвращаем как есть. */
    fun stamp(name: String): String {
        val parts = name.split("_")
        if (parts.size < 2 || parts[0].length != 10 || parts[1].length < 4) return name
        val d = parts[0].split("-")
        if (d.size != 3) return name
        val t = parts[1]
        return "${d[2]}.${d[1]} ${t.substring(0, 2)}:${t.substring(2, 4)}"
    }

    fun isTodo(line: String): Boolean = TODO.matches(line)

    /** Отмечена ли задача в строке. Для не-задачи — false. */
    fun isDone(line: String): Boolean =
        TODO.find(line)?.groupValues?.get(1)?.equals("x", ignoreCase = true) == true

    fun todoText(line: String): String? = TODO.find(line)?.groupValues?.get(2)

    /**
     * Переключить маркер в тексте файла.
     *
     * Строку ищем по ТЕКСТУ задачи, номер строки — лишь подсказка. Файл общий
     * с Obsidian и Mac: если между сканом и тапом граф дописали сверху,
     * индексы уехали, и галка встанет на соседнюю задачу — молча и не ту.
     *
     * Возвращает новый текст либо null, если задача в файле не найдена
     * (её переписали или удалили) — тогда честнее обновить список, чем гадать.
     */
    fun toggle(text: String, taskText: String, hintLine: Int): String? {
        val lines = text.split("\n").toMutableList()
        val idx = when {
            hintLine in lines.indices && lines[hintLine].contains(taskText) &&
                isTodo(lines[hintLine]) -> hintLine
            else -> lines.indexOfFirst { it.contains(taskText) && isTodo(it) }
        }
        if (idx < 0) return null
        val line = lines[idx]
        val m = TODO.find(line) ?: return null
        val markRange = m.groups[1]!!.range
        // Меняем ровно один символ маркера, не трогая остальную строку.
        val done = line.substring(markRange).equals("x", ignoreCase = true)
        lines[idx] = line.replaceRange(markRange, if (done) " " else "x")
        return lines.joinToString("\n")
    }
}
