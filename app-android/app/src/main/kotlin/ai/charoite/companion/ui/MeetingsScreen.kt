package ai.charoite.companion.ui

import ai.charoite.companion.GraphStore
import ai.charoite.companion.L
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle

@Composable
fun MeetingsScreen(onPickGraph: () -> Unit) {
    val context = LocalContext.current
    val meetings by GraphStore.meetings.collectAsStateWithLifecycle()
    val status by GraphStore.status.collectAsStateWithLifecycle()
    val loading by GraphStore.meetingsLoading.collectAsStateWithLifecycle()
    var open by remember { mutableStateOf<GraphStore.Meeting?>(null) }

    val chosen = GraphStore.folderChosen(context)

    if (!chosen) {
        Column(
            Modifier.fillMaxSize().padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                L.t(
                    "Выберите корень графа — папку, где лежит раздел «Встречи»",
                    "Choose the graph root — the folder that holds “Встречи”",
                    "请选择图谱根目录 — 包含 “Встречи” 的文件夹",
                ),
                textAlign = TextAlign.Center,
            )
            TextButton(onClick = onPickGraph) {
                Text(L.t("Выбрать папку", "Choose folder", "选择文件夹"))
            }
        }
        return
    }

    val meeting = open
    if (meeting != null) {
        var body by remember(meeting.id) { mutableStateOf<String?>(null) }
        LaunchedEffect(meeting.id) {
            body = GraphStore.text(context, meeting)
        }
        Column(Modifier.fillMaxSize().padding(16.dp).verticalScroll(rememberScrollState())) {
            TextButton(onClick = { open = null }) {
                Text(L.t("← Назад", "← Back", "← 返回"))
            }
            Text(meeting.title, style = MaterialTheme.typography.titleLarge)
            Text(meeting.stamp, style = MaterialTheme.typography.labelMedium)
            HorizontalDivider(Modifier.padding(vertical = 8.dp))
            val manifest = meeting.manifest
            if (manifest != null) {
                manifest.participants.takeIf { it.isNotEmpty() }?.let {
                    Text(L.t("Участники: ", "Participants: ", "参会者：") + it.joinToString())
                }
                manifest.summary?.let { Text(it, Modifier.padding(vertical = 8.dp)) }
                MeetingSection(L.t("Решили", "Decided", "决定"), manifest.decisions)
                MeetingSection(L.t("Поручения", "Action items", "任务"), manifest.actionItems)
                MeetingSection(
                    L.t("Открытые вопросы", "Open questions", "待解决问题"),
                    manifest.openQuestions,
                )
            } else if (body == null) {
                LinearProgressIndicator(Modifier.fillMaxWidth())
            } else {
                Text(body.orEmpty())
            }
        }
        return
    }

    Column(Modifier.fillMaxSize()) {
        if (loading) LinearProgressIndicator(Modifier.fillMaxWidth())
        status?.let { Text(it, Modifier.padding(16.dp)) }
        if (meetings.isEmpty() && !loading) {
            Text(
                L.t("Встреч пока нет", "No meetings yet", "暂无会议"),
                Modifier.padding(16.dp),
            )
        }
        LazyColumn(Modifier.fillMaxSize()) {
            items(meetings, key = { it.id }) { m ->
                Column(
                    Modifier.fillMaxWidth().clickable { open = m }.padding(16.dp)
                ) {
                    Text(m.title, style = MaterialTheme.typography.titleMedium)
                    Text(m.stamp, style = MaterialTheme.typography.labelMedium)
                }
                HorizontalDivider()
            }
        }
    }
}

@Composable
private fun MeetingSection(title: String, items: List<String>) {
    if (items.isEmpty()) return
    Text(title, style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 12.dp))
    items.forEach { Text("• $it", modifier = Modifier.padding(top = 4.dp)) }
}
