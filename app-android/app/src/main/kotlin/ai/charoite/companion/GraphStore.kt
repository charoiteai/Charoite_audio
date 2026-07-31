package ai.charoite.companion

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.DocumentsContract
import androidx.documentfile.provider.DocumentFile
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlin.coroutines.coroutineContext

/**
 * Чтение графа встреч прямо с планшета.
 *
 * Пользователь один раз выбирает корень графа (папку Obsidian-vault своего
 * проекта) — дальше лента встреч и задачи читаются из тех же markdown-файлов,
 * что видят Mac и Obsidian. Своей базы нет: файлы и есть истина.
 *
 * Обход — через ContentResolver напрямую, а не DocumentFile.listFiles():
 * на графе в несколько сотен файлов разница между «мгновенно» и «десять
 * секунд с чёрным экраном».
 */
object GraphStore {
    private const val PREFS = "charoite"
    private const val KEY_TREE = "graph.tree"
    private const val MEETINGS_DIR = "Встречи"

    data class Meeting(
        val id: String,
        val uri: Uri,
        val title: String,
        val stamp: String,
        val sortKey: String,
    )

    data class TaskItem(
        val id: String,
        val uri: Uri,
        val rel: String,
        val lineIndex: Int,
        val text: String,
        val done: Boolean,
    )

    private val _meetings = MutableStateFlow<List<Meeting>>(emptyList())
    private val _tasks = MutableStateFlow<List<TaskItem>>(emptyList())
    private val _status = MutableStateFlow<String?>(null)
    private val _meetingsLoading = MutableStateFlow(false)
    private val _tasksLoading = MutableStateFlow(false)

    private val ioScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val meetingsScan = LatestJob(ioScope)
    private val tasksScan = LatestJob(ioScope)
    private val taskWrites = Mutex()

    val meetings: StateFlow<List<Meeting>> = _meetings.asStateFlow()
    val tasks: StateFlow<List<TaskItem>> = _tasks.asStateFlow()
    val status: StateFlow<String?> = _status.asStateFlow()
    val meetingsLoading: StateFlow<Boolean> = _meetingsLoading.asStateFlow()
    val tasksLoading: StateFlow<Boolean> = _tasksLoading.asStateFlow()

    fun folderChosen(context: Context): Boolean = treeUri(context) != null

    fun folderName(context: Context): String? {
        val uri = treeUri(context) ?: return null
        return DocumentFile.fromTreeUri(context, uri)?.name
    }

    fun saveFolder(context: Context, uri: Uri) {
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
        val alive = context.contentResolver.persistedUriPermissions.any { it.uri == uri }
        if (!alive) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit().remove(KEY_TREE).apply()
            return null
        }
        return uri
    }

    /** Лента: файлы .md из папки «Встречи», свежие сверху. */
    fun rescanMeetings(context: Context) {
        val app = context.applicationContext
        meetingsScan.replace(
            onStart = { _meetingsLoading.value = true },
            onFinish = { _meetingsLoading.value = false },
        ) {
            try {
                val result = scanMeetings(app)
                _meetings.value = result.items
                _status.value = result.status
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                _status.value = L.t(
                    "Не удалось прочитать встречи",
                    "Could not read meetings",
                    "无法读取会议",
                )
            }
        }
    }

    private data class MeetingScan(val items: List<Meeting>, val status: String?)

    private suspend fun scanMeetings(context: Context): MeetingScan {
        val tree = treeUri(context)
        if (tree == null) {
            return MeetingScan(emptyList(), null)
        }
        val rootId = DocumentsContract.getTreeDocumentId(tree)
        val meetingsId = children(context, tree, rootId)
            .firstOrNull { it.isDir && it.name == MEETINGS_DIR }?.documentId
        if (meetingsId == null) {
            return MeetingScan(
                emptyList(),
                L.t(
                    "В выбранной папке нет раздела «Встречи» — это корень графа?",
                    "No “Встречи” folder inside — is this the graph root?",
                    "所选文件夹内没有 “Встречи” — 这是图谱根目录吗？",
                ),
            )
        }
        val out = children(context, tree, meetingsId)
            .filter { !it.isDir && it.name.endsWith(".md") }
            .map { entry ->
                coroutineContext.ensureActive()
                val name = entry.name.removeSuffix(".md")
                val text = read(context, entry.uri).orEmpty()
                Meeting(
                    id = "$MEETINGS_DIR/${entry.name}",
                    uri = entry.uri,
                    title = GraphText.title(text, name),
                    stamp = GraphText.stamp(name),
                    sortKey = name,
                )
            }
            .sortedByDescending { it.sortKey }
        return MeetingScan(out, null)
    }

    /** Задачи `- [ ]` по всему графу — как на Mac, открытые сверху. */
    fun rescanTasks(context: Context) {
        val app = context.applicationContext
        tasksScan.replace(
            onStart = { _tasksLoading.value = true },
            onFinish = { _tasksLoading.value = false },
        ) {
            try {
                _tasks.value = scanTasks(app)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                _status.value = L.t(
                    "Не удалось прочитать задачи",
                    "Could not read tasks",
                    "无法读取任务",
                )
            }
        }
    }

    private suspend fun scanTasks(context: Context): List<TaskItem> {
        val tree = treeUri(context)
        if (tree == null) {
            return emptyList()
        }
        val found = mutableListOf<TaskItem>()
        val active = coroutineContext
        walk(context, tree, DocumentsContract.getTreeDocumentId(tree), "") { entry, rel ->
            // Обход задач тяжелее ленты встреч — он читает ВЕСЬ граф. Без этой
            // проверки отменённый скан продолжает молотить файлы до конца, и
            // переключение вкладок туда-обратно множит их друг на друга.
            active.ensureActive()
            if (!entry.name.endsWith(".md")) return@walk
            val text = read(context, entry.uri) ?: return@walk
            if (!text.contains("- [")) return@walk
            text.split("\n").forEachIndexed { i, line ->
                val body = GraphText.todoText(line) ?: return@forEachIndexed
                found += TaskItem(
                    id = "$rel#$i", uri = entry.uri, rel = rel, lineIndex = i,
                    text = body, done = GraphText.isDone(line),
                )
            }
        }
        return found.sortedBy { it.done }
    }

    fun openCount(): Int = _tasks.value.count { !it.done }

    /** Отметка — точечная замена маркера, файл остаётся истиной для всех. */
    fun toggle(context: Context, item: TaskItem) {
        val app = context.applicationContext
        ioScope.launch {
            try {
                taskWrites.withLock { toggleBlocking(app, item) }
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                _status.value = L.t(
                    "Не удалось отметить задачу",
                    "Could not update the task",
                    "无法更新任务",
                )
            }
            rescanTasks(app)
        }
    }

    private fun toggleBlocking(context: Context, item: TaskItem) {
        val text = read(context, item.uri)
        if (text == null) {
            _status.value = L.t(
                "Файл не читается — синхронизация ещё идёт?",
                "File is not readable — sync still running?",
                "文件无法读取 — 同步仍在进行？",
            )
            return
        }
        val updated = GraphText.toggle(text, item.text, item.lineIndex)
        if (updated == null) {
            _status.value = L.t(
                "Задача изменилась в графе — список обновлён",
                "Task changed in the graph — list refreshed",
                "任务在图谱中已变更 — 列表已刷新",
            )
            return
        }
        val ok = runCatching {
            // "wt" — перезапись с усечением: без t остаётся хвост старого
            // файла, если новый текст короче.
            context.contentResolver.openOutputStream(item.uri, "wt").use { out ->
                checkNotNull(out) { "no stream" }
                out.write(updated.toByteArray(Charsets.UTF_8))
            }
        }.isSuccess
        if (!ok) {
            _status.value = L.t(
                "Не удалось отметить задачу",
                "Could not update the task",
                "无法更新任务",
            )
        }
    }

    suspend fun text(context: Context, meeting: Meeting): String = withContext(Dispatchers.IO) {
        read(context.applicationContext, meeting.uri)
            ?: L.t("Файл не читается", "File is unreadable", "文件无法读取")
    }

    // --- обход дерева -----------------------------------------------------

    private data class Entry(val documentId: String, val name: String, val isDir: Boolean, val uri: Uri)

    private fun children(context: Context, tree: Uri, parentId: String): List<Entry> {
        val uri = DocumentsContract.buildChildDocumentsUriUsingTree(tree, parentId)
        val cols = arrayOf(
            DocumentsContract.Document.COLUMN_DOCUMENT_ID,
            DocumentsContract.Document.COLUMN_DISPLAY_NAME,
            DocumentsContract.Document.COLUMN_MIME_TYPE,
        )
        val out = mutableListOf<Entry>()
        runCatching {
            context.contentResolver.query(uri, cols, null, null, null)?.use { c ->
                while (c.moveToNext()) {
                    val id = c.getString(0)
                    val name = c.getString(1) ?: continue
                    val mime = c.getString(2)
                    out += Entry(
                        documentId = id,
                        name = name,
                        isDir = mime == DocumentsContract.Document.MIME_TYPE_DIR,
                        uri = DocumentsContract.buildDocumentUriUsingTree(tree, id),
                    )
                }
            }
        }
        return out
    }

    private suspend fun walk(
        context: Context,
        tree: Uri,
        parentId: String,
        prefix: String,
        visit: suspend (Entry, String) -> Unit,
    ) {
        for (e in children(context, tree, parentId)) {
            coroutineContext.ensureActive()
            if (e.name.startsWith(".")) continue          // .obsidian и прочая служебка
            val rel = if (prefix.isEmpty()) e.name else "$prefix/${e.name}"
            if (e.isDir) walk(context, tree, e.documentId, rel, visit) else visit(e, rel)
        }
    }

    private fun read(context: Context, uri: Uri): String? = runCatching {
        context.contentResolver.openInputStream(uri)?.use { it.readBytes().toString(Charsets.UTF_8) }
    }.getOrNull()
}
