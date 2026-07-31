package ai.charoite.companion

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import androidx.documentfile.provider.DocumentFile
import java.io.File
import kotlin.concurrent.thread

/**
 * Доставка записи на Mac.
 *
 * Путь один: папка, которую пользователь один раз выбрал через системный
 * выбор (SAF) — та же, куда смотрит папка импорта macOS-приложения.
 * Синхронизирует её что угодно (Syncthing, облачный клиент) — приложению
 * всё равно, оно просто кладёт файл. Прямая доставка по Wi-Fi ждёт своего
 * протокола (см. ROADMAP), а до тех пор изобретать свой обмен нечестно.
 *
 * Недоставленное не пропадает: очередь во внутренней памяти приложения,
 * досылка при каждом запуске и после каждого стопа.
 */
object Outbox {
    private const val PREFS = "charoite"
    private const val KEY_TREE = "inbox.tree"
    private val AUDIO_EXT = setOf("wav", "m4a")

    @Volatile private var flushing = false

    fun dir(context: Context): File =
        File(context.filesDir, "Outbox").apply { mkdirs() }

    /**
     * Куда пишется ИДУЩАЯ запись. Отдельная папка, чтобы досылка не утащила
     * файл, который прямо сейчас пишется.
     */
    fun inProgress(context: Context): File =
        File(dir(context), "current").apply { mkdirs() }

    fun queuedCount(context: Context): Int =
        dir(context).listFiles()?.count { it.extension.lowercase() in AUDIO_EXT } ?: 0

    fun folderChosen(context: Context): Boolean = treeUri(context) != null

    /** Человекочитаемое имя выбранной папки — для строки в настройках. */
    fun folderName(context: Context): String? {
        val uri = treeUri(context) ?: return null
        return DocumentFile.fromTreeUri(context, uri)?.name
    }

    fun saveFolder(context: Context, uri: Uri) {
        // Разрешение должно пережить перезагрузку — иначе очередь копится,
        // а человек не понимает, почему ничего не уезжает.
        context.contentResolver.takePersistableUriPermission(
            uri,
            Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION,
        )
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_TREE, uri.toString()).apply()
    }

    private fun treeUri(context: Context): Uri? {
        val saved = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString(KEY_TREE, null) ?: return null
        val uri = Uri.parse(saved)
        // Разрешение могли отозвать (переустановка, очистка данных, папку
        // удалили). Забываем — UI позовёт выбрать заново.
        val alive = context.contentResolver.persistedUriPermissions.any {
            it.uri == uri && it.isWritePermission
        }
        if (!alive) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit().remove(KEY_TREE).apply()
            return null
        }
        return uri
    }

    /**
     * Записи, пережившие смерть приложения: их никто не закрыл и не поставил
     * в очередь. Заголовок такого WAV отстаёт от факта на пару секунд —
     * чиним и отправляем. Зовётся на старте.
     */
    fun rescueOrphans(context: Context) {
        val left = inProgress(context).listFiles() ?: return
        for (f in left) {
            if (f.extension.lowercase() !in AUDIO_EXT) continue
            WavWriter.repair(f)
            f.renameTo(uniqueFile(dir(context), f.name))
        }
    }

    /** Файл — в очередь, затем попытка доставки всей очереди. */
    fun deliver(context: Context, file: File) {
        val queued = uniqueFile(dir(context), file.name)
        if (!file.renameTo(queued)) {
            RecorderState.say(
                L.t(
                    "Не удалось поставить запись в очередь",
                    "Could not queue the recording",
                    "无法将录音加入队列",
                )
            )
            return
        }
        flush(context)
    }

    /**
     * Дослать всё из очереди. Публикация атомарная: пишем во временное имя
     * и переименовываем. Сканер на Mac отбирает файлы по расширению и не
     * проверяет, дописан ли файл, — попадание его таймера в окно копирования
     * даст расшифровку половины разговора без права на повтор.
     */
    fun flush(context: Context, onDone: (() -> Unit)? = null) {
        if (flushing) return
        val app = context.applicationContext
        flushing = true
        thread(name = "charoite-outbox") {
            try {
                flushBlocking(app)
            } finally {
                flushing = false
                onDone?.invoke()
            }
        }
    }

    private fun flushBlocking(context: Context) {
        val files = dir(context).listFiles()
            ?.filter { it.isFile && it.extension.lowercase() in AUDIO_EXT }
            ?.sortedBy { it.name }
            ?: return
        if (files.isEmpty()) return

        val tree = treeUri(context)
        if (tree == null) {
            RecorderState.say(
                L.t(
                    "Выберите папку доставки — записей в очереди: ${files.size}",
                    "Choose the delivery folder — queued: ${files.size}",
                    "请选择投递文件夹 — 队列中：${files.size}",
                )
            )
            return
        }
        val root = DocumentFile.fromTreeUri(context, tree)
        if (root == null || !root.canWrite()) {
            RecorderState.say(
                L.t(
                    "Папка доставки недоступна — выберите её заново",
                    "Delivery folder unavailable — pick it again",
                    "投递文件夹不可用 — 请重新选择",
                )
            )
            return
        }

        var sent = 0
        for (f in files) {
            val ok = runCatching { publish(context, root, f) }.getOrElse { e ->
                RecorderState.say(
                    L.t(
                        "Не отправилось (${f.name}): ${e.message}",
                        "Failed (${f.name}): ${e.message}",
                        "发送失败（${f.name}）：${e.message}",
                    )
                )
                false
            }
            // continue, а не выход: один сбойный файл не должен запирать
            // очередь вместе с сегодняшней встречей.
            if (ok) {
                f.delete()
                sent++
            }
        }
        val left = queuedCount(context)
        RecorderState.say(
            if (left == 0) {
                L.t("Уехало на Mac: $sent", "Delivered to Mac: $sent", "已发送到 Mac：$sent")
            } else {
                L.t(
                    "Отправлено $sent, в очереди $left",
                    "Sent $sent, queued $left",
                    "已发送 $sent，队列中 $left",
                )
            }
        )
    }

    /**
     * Один файл: временное имя → байты → переименование в целевое.
     *
     * Если провайдер папки не умеет переименовывать (редкость, но бывает у
     * облачных клиентов), пишем сразу под целевым именем и говорим об этом:
     * лучше маленькое окно недописанного файла, чем совсем не доставить.
     */
    private fun publish(context: Context, root: DocumentFile, file: File): Boolean {
        val finalName = uniqueName(root, file.name)
        val tmpName = "$finalName.part"
        root.findFile(tmpName)?.delete()

        val doc = root.createFile("application/octet-stream", tmpName)
            ?: throw IllegalStateException(
                L.t("папка не принимает файлы", "folder rejects files", "文件夹拒绝写入")
            )
        context.contentResolver.openOutputStream(doc.uri, "w").use { out ->
            checkNotNull(out) { "no stream" }
            file.inputStream().use { it.copyTo(out, 256 * 1024) }
        }

        val renamed = runCatching {
            DocumentsContract.renameDocument(context.contentResolver, doc.uri, finalName)
        }.getOrNull()
        if (renamed == null) {
            // Провайдер без переименования: публикуем как есть, но целевым
            // именем — иначе файл с расширением .part Mac просто не увидит.
            val direct = root.createFile("application/octet-stream", finalName)
                ?: throw IllegalStateException(
                    L.t("папка не принимает файлы", "folder rejects files", "文件夹拒绝写入")
                )
            context.contentResolver.openOutputStream(direct.uri, "w").use { out ->
                checkNotNull(out) { "no stream" }
                file.inputStream().use { it.copyTo(out, 256 * 1024) }
            }
            doc.delete()
        }
        return true
    }

    /**
     * Свободное имя рядом с занятым. Затирать чужой файл нельзя нигде: ни в
     * своей очереди, ни в папке доставки.
     */
    internal fun uniqueName(root: DocumentFile, name: String): String {
        if (root.findFile(name) == null) return name
        val base = name.substringBeforeLast('.')
        val ext = name.substringAfterLast('.', "")
        var n = 1
        while (true) {
            val candidate = if (ext.isEmpty()) "$base-$n" else "$base-$n.$ext"
            if (root.findFile(candidate) == null) return candidate
            n++
        }
    }

    internal fun uniqueFile(dir: File, name: String): File {
        var candidate = File(dir, name)
        if (!candidate.exists()) return candidate
        val base = name.substringBeforeLast('.')
        val ext = name.substringAfterLast('.', "")
        var n = 1
        while (candidate.exists()) {
            candidate = File(dir, if (ext.isEmpty()) "$base-$n" else "$base-$n.$ext")
            n++
        }
        return candidate
    }
}
