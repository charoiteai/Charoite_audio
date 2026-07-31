package ai.charoite.companion.ui

import ai.charoite.companion.GraphStore
import ai.charoite.companion.L
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle

@Composable
fun TasksScreen(onPickGraph: () -> Unit) {
    val context = LocalContext.current
    val tasks by GraphStore.tasks.collectAsStateWithLifecycle()
    val status by GraphStore.status.collectAsStateWithLifecycle()
    val loading by GraphStore.tasksLoading.collectAsStateWithLifecycle()

    if (!GraphStore.folderChosen(context)) {
        Column(
            Modifier.fillMaxSize().padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                L.t(
                    "Выберите корень графа — задачи читаются из его markdown-файлов",
                    "Choose the graph root — tasks are read from its markdown files",
                    "请选择图谱根目录 — 任务读取自其 markdown 文件",
                ),
                textAlign = TextAlign.Center,
            )
            TextButton(onClick = onPickGraph) {
                Text(L.t("Выбрать папку", "Choose folder", "选择文件夹"))
            }
        }
        return
    }

    Column(Modifier.fillMaxSize()) {
        if (loading) LinearProgressIndicator(Modifier.fillMaxWidth())
        status?.let { Text(it, Modifier.padding(16.dp)) }
        Text(
            L.t(
                "Открытых: ${GraphStore.openCount()}",
                "Open: ${GraphStore.openCount()}",
                "未完成：${GraphStore.openCount()}",
            ),
            Modifier.padding(16.dp),
            style = MaterialTheme.typography.labelLarge,
        )
        LazyColumn(Modifier.fillMaxSize()) {
            items(tasks, key = { it.id }) { t ->
                Row(
                    Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Checkbox(checked = t.done, onCheckedChange = { GraphStore.toggle(context, t) })
                    Column(Modifier.padding(start = 4.dp)) {
                        Text(t.text)
                        Text(t.rel, style = MaterialTheme.typography.labelSmall)
                    }
                }
                HorizontalDivider()
            }
        }
    }
}
