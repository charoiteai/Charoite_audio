package ai.charoite.companion

import android.content.Context
import java.util.Locale

/**
 * Локализация компаньона. Тот же приём, что на Mac и iPhone: тройка
 * переводов на месте вызова, без словаря придуманных ключей.
 *
 * Источник языка — системная локаль с возможностью переопределить руками:
 * человек, ведущий встречи по-английски на русском планшете, выберет сам.
 */
object L {
    private const val PREFS = "charoite"
    private const val KEY = "ui.language"
    private val supported = setOf("ru", "en", "zh")

    /** Пусто — значит по системной локали. */
    @Volatile
    private var override: String = ""

    fun load(context: Context) {
        override = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY, "").orEmpty()
    }

    fun setOverride(context: Context, value: String) {
        override = if (value in supported) value else ""
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY, override).apply()
    }

    fun override(): String = override

    fun lang(): String {
        if (override in supported) return override
        return when (Locale.getDefault().language.lowercase()) {
            "ru" -> "ru"
            "zh" -> "zh"
            else -> "en"
        }
    }

    fun t(ru: String, en: String, zh: String): String = when (lang()) {
        "ru" -> ru
        "zh" -> zh
        else -> en
    }
}
