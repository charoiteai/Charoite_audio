package ai.charoite.companion.ui

import ai.charoite.companion.GraphStore
import ai.charoite.companion.L
import ai.charoite.companion.Outbox
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp

@Composable
fun SettingsScreen(
    onPickInbox: () -> Unit,
    onPickGraph: () -> Unit,
    onLanguage: (String) -> Unit,
) {
    val context = LocalContext.current

    Column(
        Modifier.fillMaxSize().padding(20.dp).verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            L.t("Папка доставки", "Delivery folder", "投递文件夹"),
            style = MaterialTheme.typography.titleMedium,
        )
        Text(
            Outbox.folderName(context)
                ?: L.t("не выбрана", "not chosen", "未选择"),
            style = MaterialTheme.typography.bodyMedium,
        )
        Text(
            L.t(
                "Сюда кладутся записи. Ту же папку должен видеть Mac — через " +
                    "Syncthing или любой синхронизатор; на Mac это папка импорта.",
                "Recordings land here. The Mac must see the same folder — through " +
                    "Syncthing or any sync tool; on the Mac it is the import folder.",
                "录音将保存在此处。Mac 需要通过 Syncthing 或任意同步工具看到同一文件夹；" +
                    "在 Mac 上它是导入文件夹。",
            ),
            style = MaterialTheme.typography.bodySmall,
        )
        Button(onClick = onPickInbox) {
            Text(L.t("Выбрать", "Choose", "选择"))
        }

        HorizontalDivider()

        Text(
            L.t("Корень графа", "Graph root", "图谱根目录"),
            style = MaterialTheme.typography.titleMedium,
        )
        Text(
            GraphStore.folderName(context)
                ?: L.t("не выбран", "not chosen", "未选择"),
            style = MaterialTheme.typography.bodyMedium,
        )
        Text(
            L.t(
                "Папка Obsidian-графа: из неё читаются лента встреч и задачи. " +
                    "Отметки пишутся прямо в markdown — Mac и Obsidian увидят их сразу.",
                "The Obsidian graph folder: the meetings feed and tasks are read from it. " +
                    "Ticks are written into the markdown itself — the Mac and Obsidian see them at once.",
                "Obsidian 图谱文件夹：会议列表和任务从中读取。" +
                    "勾选直接写入 markdown — Mac 和 Obsidian 立即可见。",
            ),
            style = MaterialTheme.typography.bodySmall,
        )
        Button(onClick = onPickGraph) {
            Text(L.t("Выбрать", "Choose", "选择"))
        }

        HorizontalDivider()

        Text(L.t("Язык", "Language", "语言"), style = MaterialTheme.typography.titleMedium)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("" to L.t("Системный", "System", "系统"), "ru" to "Русский", "en" to "English", "zh" to "中文")
                .forEach { (code, label) ->
                    FilterChip(
                        selected = L.override() == code,
                        onClick = { onLanguage(code) },
                        label = { Text(label) },
                    )
                }
        }

        HorizontalDivider()

        Text(
            L.t(
                "Приватность: приложение не имеет сетевых разрешений вовсе. " +
                    "Звук не покидает планшет иначе как через выбранную вами папку.",
                "Privacy: the app holds no network permissions at all. " +
                    "Audio leaves the tablet only through the folder you picked.",
                "隐私：本应用完全没有网络权限。" +
                    "音频只能通过你选择的文件夹离开平板。",
            ),
            style = MaterialTheme.typography.bodySmall,
        )
    }
}
