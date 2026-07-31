package ai.charoite.companion.ui

import ai.charoite.companion.L
import ai.charoite.companion.Outbox
import ai.charoite.companion.RecordKind
import ai.charoite.companion.RecorderService
import ai.charoite.companion.RecorderState
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle

@Composable
fun RecordScreen(micGranted: Boolean, onAskMic: () -> Unit, onPickInbox: () -> Unit) {
    val context = LocalContext.current
    val recording by RecorderState.recording.collectAsStateWithLifecycle()
    val elapsed by RecorderState.elapsed.collectAsStateWithLifecycle()
    val level by RecorderState.level.collectAsStateWithLifecycle()
    val activeKind by RecorderState.kind.collectAsStateWithLifecycle()
    val message by RecorderState.message.collectAsStateWithLifecycle()
    var kind by remember { mutableStateOf(RecordKind.MEETING) }

    Column(
        Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            RecordKind.entries.forEach { k ->
                FilterChip(
                    selected = (if (recording) activeKind else kind) == k,
                    onClick = { if (!recording) kind = k },
                    enabled = !recording,
                    label = { Text(k.title()) },
                )
            }
        }

        Spacer(Modifier.height(32.dp))

        Text(
            text = clock(elapsed),
            fontSize = 48.sp,
            color = MaterialTheme.colorScheme.onBackground,
        )

        Spacer(Modifier.height(12.dp))

        // Полоска уровня: единственный способ увидеть, что микрофон реально
        // слышит комнату, а не пишет тишину из-за занятого другим приложением
        // входа.
        Box(
            Modifier.fillMaxWidth().height(8.dp)
                .clip(RoundedCornerShape(4.dp))
                .background(MaterialTheme.colorScheme.surface)
        ) {
            Box(
                Modifier.fillMaxWidth(if (recording) level else 0f).height(8.dp)
                    .background(MaterialTheme.colorScheme.primary)
            )
        }

        Spacer(Modifier.height(32.dp))

        Button(
            onClick = {
                when {
                    recording -> RecorderService.stop(context)
                    !micGranted -> onAskMic()
                    else -> RecorderService.start(context, kind)
                }
            },
            colors = ButtonDefaults.buttonColors(
                containerColor = if (recording) Color(0xFFD1435B) else MaterialTheme.colorScheme.primary
            ),
            modifier = Modifier.fillMaxWidth().height(64.dp),
        ) {
            Text(
                text = when {
                    recording -> L.t("Стоп", "Stop", "停止")
                    !micGranted -> L.t("Разрешить микрофон", "Allow microphone", "允许麦克风")
                    else -> L.t("Запись", "Record", "录音")
                },
                fontSize = 20.sp,
            )
        }

        Spacer(Modifier.height(24.dp))

        if (!Outbox.folderChosen(context)) {
            Text(
                L.t(
                    "Папка доставки не выбрана — записи будут копиться в очереди",
                    "No delivery folder yet — recordings will pile up in the queue",
                    "尚未选择投递文件夹 — 录音将留在队列中",
                ),
                textAlign = TextAlign.Center,
                color = MaterialTheme.colorScheme.onBackground,
            )
            TextButton(onClick = onPickInbox) {
                Text(L.t("Выбрать папку", "Choose folder", "选择文件夹"))
            }
        } else {
            val queued = Outbox.queuedCount(context)
            if (queued > 0) {
                Text(
                    L.t("В очереди: $queued", "Queued: $queued", "队列中：$queued"),
                    color = MaterialTheme.colorScheme.onBackground,
                )
                TextButton(onClick = { Outbox.flush(context) }) {
                    Text(L.t("Дослать", "Send now", "立即发送"))
                }
            }
        }

        message?.let {
            Spacer(Modifier.height(12.dp))
            Text(it, textAlign = TextAlign.Center, color = MaterialTheme.colorScheme.onBackground)
        }
    }
}

private fun clock(seconds: Double): String {
    val total = seconds.toInt()
    return "%02d:%02d:%02d".format(total / 3600, (total % 3600) / 60, total % 60)
}
