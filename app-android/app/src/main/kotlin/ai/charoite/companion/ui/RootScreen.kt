package ai.charoite.companion.ui

import ai.charoite.companion.GraphStore
import ai.charoite.companion.L
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext

@Composable
fun RootScreen(
    micGranted: Boolean,
    onAskMic: () -> Unit,
    onPickInbox: () -> Unit,
    onPickGraph: () -> Unit,
    onLanguage: (String) -> Unit,
) {
    var tab by remember { mutableIntStateOf(0) }
    val context = LocalContext.current
    val titles = listOf(
        L.t("Запись", "Record", "录音"),
        L.t("Встречи", "Meetings", "会议"),
        L.t("Задачи", "Tasks", "任务"),
        L.t("Настройки", "Settings", "设置"),
    )

    Scaffold { pad ->
        Column(Modifier.fillMaxSize().padding(pad)) {
            TabRow(selectedTabIndex = tab) {
                titles.forEachIndexed { i, title ->
                    Tab(
                        selected = tab == i,
                        onClick = {
                            tab = i
                            // Граф читаем при входе на вкладку: сканировать
                            // сотни файлов «на всякий случай» — греть планшет.
                            if (i == 1) GraphStore.rescanMeetings(context)
                            if (i == 2) GraphStore.rescanTasks(context)
                        },
                        text = { Text(title) },
                    )
                }
            }
            when (tab) {
                0 -> RecordScreen(micGranted = micGranted, onAskMic = onAskMic, onPickInbox = onPickInbox)
                1 -> MeetingsScreen(onPickGraph = onPickGraph)
                2 -> TasksScreen(onPickGraph = onPickGraph)
                else -> SettingsScreen(
                    onPickInbox = onPickInbox,
                    onPickGraph = onPickGraph,
                    onLanguage = onLanguage,
                )
            }
        }
    }
}
