package ai.charoite.companion

import android.Manifest
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import ai.charoite.companion.ui.RootScreen

/**
 * Единственный экран приложения. Разрешения спрашиваем ДО первой записи:
 * AudioRecord при запрещённом микрофоне обычно не падает, а тихо отдаёт
 * тишину — человек пишет час и получает пустой файл вместо встречи.
 */
class MainActivity : ComponentActivity() {

    private var micGranted by mutableStateOf(false)

    /** Растёт при смене языка: заставляет Compose перерисовать все подписи. */
    private var langTick by mutableIntStateOf(0)

    private val askMic = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
        micGranted = it
        if (!it) {
            RecorderState.say(
                L.t(
                    "Без доступа к микрофону запись невозможна",
                    "Recording needs microphone access",
                    "录音需要麦克风权限",
                )
            )
        }
    }

    private val askNotifications =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    private val pickInbox =
        registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri: Uri? ->
            if (uri != null) {
                Outbox.saveFolder(this, uri)
                Outbox.flush(this)
            }
        }

    private val pickGraph =
        registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri: Uri? ->
            if (uri != null) {
                GraphStore.saveFolder(this, uri)
                GraphStore.rescanMeetings(this)
                GraphStore.rescanTasks(this)
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        L.load(this)
        micGranted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED

        // Записи, пережившие смерть приложения, и всё, что не уехало раньше.
        Outbox.rescueOrphans(this)
        Outbox.flush(this)

        setContent {
            @Suppress("UNUSED_EXPRESSION")
            langTick   // читаем — иначе смена языка не перерисует экран
            CharoiteTheme {
                RootScreen(
                    micGranted = micGranted,
                    onAskMic = { askMic.launch(Manifest.permission.RECORD_AUDIO) },
                    onPickInbox = { pickInbox.launch(null) },
                    onPickGraph = { pickGraph.launch(null) },
                    onLanguage = { code ->
                        L.setOverride(this, code)
                        langTick++
                    },
                )
            }
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    override fun onResume() {
        super.onResume()
        // Папку могли синхронизировать, пока приложение висело в фоне.
        if (!RecorderState.recording.value) Outbox.flush(this)
    }
}
