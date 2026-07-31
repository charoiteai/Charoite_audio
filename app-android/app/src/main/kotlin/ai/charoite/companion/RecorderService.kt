package ai.charoite.companion

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import android.os.StatFs
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.concurrent.thread
import kotlin.math.abs
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Что пишем. rawValue уходит в префикс имени файла — по нему Mac выбирает конвейер. */
enum class RecordKind(val id: String, val prefix: String) {
    MEETING("meeting", ""),
    NOTE("note", "note_"),
    DIARY("diary", "diary_");

    fun title(): String = when (this) {
        MEETING -> L.t("Встреча", "Meeting", "会议")
        NOTE -> L.t("Заметка", "Note", "笔记")
        DIARY -> L.t("Дневник", "Diary", "日记")
    }
}

/**
 * Состояние записи для экрана. Источник истины — сервис: он переживает
 * сворачивание приложения, а Activity может умереть и родиться заново.
 */
object RecorderState {
    private val _recording = MutableStateFlow(false)
    private val _elapsed = MutableStateFlow(0.0)
    private val _level = MutableStateFlow(0f)
    private val _kind = MutableStateFlow(RecordKind.MEETING)
    private val _message = MutableStateFlow<String?>(null)

    val recording: StateFlow<Boolean> = _recording.asStateFlow()
    val elapsed: StateFlow<Double> = _elapsed.asStateFlow()
    val level: StateFlow<Float> = _level.asStateFlow()
    val kind: StateFlow<RecordKind> = _kind.asStateFlow()
    val message: StateFlow<String?> = _message.asStateFlow()

    internal fun started(kind: RecordKind) {
        _kind.value = kind
        _recording.value = true
        _elapsed.value = 0.0
        _level.value = 0f
        _message.value = null
    }

    internal fun tick(seconds: Double, level: Float) {
        _elapsed.value = seconds
        _level.value = level
    }

    internal fun stopped() {
        _recording.value = false
        _level.value = 0f
    }

    fun say(text: String?) {
        _message.value = text
    }
}

/**
 * Фоновая запись встречи.
 *
 * Foreground-сервис с типом microphone — единственный способ на Android
 * дописать час разговора, когда экран погас и приложение свернули.
 * Стартуем только с экрана: из фона система запуск микрофонного сервиса
 * запрещает (Android 12+), и это правильно.
 */
class RecorderService : Service() {

    private var recorder: AudioRecord? = null
    private var writer: WavWriter? = null
    private var worker: Thread? = null
    private var target: File? = null
    @Volatile private var running = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                val kind = RecordKind.entries
                    .firstOrNull { it.id == intent.getStringExtra(EXTRA_KIND) } ?: RecordKind.MEETING
                start(kind)
            }
            ACTION_STOP -> stop()
            else -> stopSelf()
        }
        return START_NOT_STICKY
    }

    private fun start(kind: RecordKind) {
        if (running) return

        if (freeBytes() < MIN_FREE_BYTES) {
            RecorderState.say(
                L.t(
                    "Мало места — освободите 500 МБ",
                    "Low storage — free up 500 MB",
                    "存储空间不足 — 请释放 500 MB",
                )
            )
            stopSelf()
            return
        }

        val rate = WavWriter.SAMPLE_RATE
        val minBuf = AudioRecord.getMinBufferSize(
            rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        if (minBuf <= 0) {
            RecorderState.say(
                L.t(
                    "Микрофон недоступен на этом устройстве",
                    "Microphone unavailable on this device",
                    "此设备麦克风不可用",
                )
            )
            stopSelf()
            return
        }
        val bufBytes = maxOf(minBuf * 2, rate * 2 / 2)   // не меньше полусекунды звука

        val rec = try {
            AudioRecord(
                audioSource(), rate, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, bufBytes
            )
        } catch (e: SecurityException) {
            // Разрешение спрашивает экран; сюда попадаем, если его отозвали
            // между нажатием и стартом сервиса.
            RecorderState.say(
                L.t(
                    "Нет доступа к микрофону: разрешите в настройках",
                    "Microphone access denied: allow it in settings",
                    "麦克风权限被拒绝：请在设置中允许",
                )
            )
            stopSelf()
            return
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            RecorderState.say(
                L.t(
                    "Запись не стартовала — микрофон занят другим приложением?",
                    "Recording did not start — microphone busy?",
                    "录音未开始 — 麦克风被占用？",
                )
            )
            stopSelf()
            return
        }

        val file = File(Outbox.inProgress(this), "${kind.prefix}android_${stamp(Date())}.wav")
        val w = WavWriter(file)
        recorder = rec
        writer = w
        target = file
        running = true

        startForegroundNotification(kind)
        rec.startRecording()
        RecorderState.started(kind)

        worker = thread(name = "charoite-rec") { pump(rec, w) }
    }

    /**
     * Источник звука. UNPROCESSED — без автоусиления и шумодава, которые на
     * встрече съедают дальнего собеседника и портят распознавание (аналог
     * режима .measurement на iPhone). Поддерживается не всеми прошивками —
     * честно спрашиваем систему и откатываемся.
     */
    private fun audioSource(): Int {
        val am = getSystemService(Context.AUDIO_SERVICE) as? AudioManager
        val unprocessed = am?.getProperty(AudioManager.PROPERTY_SUPPORT_AUDIO_SOURCE_UNPROCESSED)
        return if (unprocessed?.equals("true", ignoreCase = true) == true) {
            MediaRecorder.AudioSource.UNPROCESSED
        } else {
            MediaRecorder.AudioSource.VOICE_RECOGNITION
        }
    }

    private fun pump(rec: AudioRecord, w: WavWriter) {
        val buf = ByteArray(CHUNK_BYTES)
        var lastTick = 0L
        while (running) {
            val read = rec.read(buf, 0, buf.size)
            if (read <= 0) {
                if (read == AudioRecord.ERROR_INVALID_OPERATION ||
                    read == AudioRecord.ERROR_DEAD_OBJECT
                ) {
                    // Микрофон отобрали (звонок, другое приложение). Записанное
                    // сохраняем и честно говорим — молча копить тишину нельзя.
                    RecorderState.say(
                        L.t(
                            "Запись оборвалась — сохраняю записанное",
                            "Recording broke — saving what we have",
                            "录音中断 — 正在保存已录内容",
                        )
                    )
                    stopFromWorker()
                    return
                }
                continue
            }
            try {
                w.write(buf, read)
            } catch (e: Exception) {
                RecorderState.say(
                    L.t(
                        "Сбой записи на диск: ${e.message}",
                        "Disk write failed: ${e.message}",
                        "写入磁盘失败：${e.message}",
                    )
                )
                stopFromWorker()
                return
            }
            val now = System.currentTimeMillis()
            if (now - lastTick >= 200) {
                lastTick = now
                RecorderState.tick(w.seconds(), level(buf, read))
            }
        }
    }

    /** Пиковый уровень 0…1 для полоски на экране. */
    private fun level(buf: ByteArray, read: Int): Float {
        var peak = 0
        var i = 0
        while (i + 1 < read) {
            val v = abs(((buf[i + 1].toInt() shl 8) or (buf[i].toInt() and 0xff)).toShort().toInt())
            if (v > peak) peak = v
            i += 2
        }
        return (peak / 32_768f).coerceIn(0f, 1f)
    }

    private fun stopFromWorker() {
        running = false
        // Останавливаем себя снаружи рабочего потока: stopSelf синхронно
        // дождётся его join и получит дедлок.
        android.os.Handler(mainLooper).post { stop() }
    }

    private fun stop() {
        if (recorder == null && writer == null) {
            stopSelf()
            return
        }
        running = false
        worker?.join(2_000)
        worker = null

        recorder?.let {
            runCatching { it.stop() }
            it.release()
        }
        recorder = null

        val seconds = writer?.seconds() ?: 0.0
        runCatching { writer?.close() }
        writer = null

        val file = target
        target = null
        RecorderState.stopped()

        if (file != null) {
            if (seconds < MIN_USEFUL_SECONDS) {
                // Случайное касание: пустышка засорила бы папку импорта и
                // родила бы на Mac встречу из тишины.
                file.delete()
                RecorderState.say(
                    L.t("Слишком коротко — не сохраняю", "Too short — discarded", "太短 — 已丢弃")
                )
            } else {
                Outbox.deliver(this, file)
            }
        }

        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        running = false
        super.onDestroy()
    }

    private fun startForegroundNotification(kind: RecordKind) {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(
                NotificationChannel(
                    CHANNEL, L.t("Запись", "Recording", "录音"),
                    NotificationManager.IMPORTANCE_LOW
                ).apply {
                    description = L.t(
                        "Таймер идущей записи",
                        "Timer of the running recording",
                        "正在录音的计时器",
                    )
                    setShowBadge(false)
                }
            )
        }
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, RecorderService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE
        )
        val n = Notification.Builder(this, CHANNEL)
            .setContentTitle(L.t("Идёт запись", "Recording", "正在录音"))
            .setContentText(kind.title())
            .setSmallIcon(android.R.drawable.presence_audio_online)
            .setUsesChronometer(true)      // таймер прямо на локскрине
            .setOngoing(true)
            .setContentIntent(open)
            .addAction(
                Notification.Action.Builder(
                    null, L.t("Стоп", "Stop", "停止"), stop
                ).build()
            )
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(NOTIFICATION_ID, n)
        }
    }

    private fun freeBytes(): Long {
        val stat = StatFs(filesDir.absolutePath)
        return stat.availableBlocksLong * stat.blockSizeLong
    }

    companion object {
        const val ACTION_START = "ai.charoite.companion.START"
        const val ACTION_STOP = "ai.charoite.companion.STOP"
        const val EXTRA_KIND = "kind"

        private const val CHANNEL = "recording"
        private const val NOTIFICATION_ID = 1
        private const val CHUNK_BYTES = 8_192

        /** Час записи ≈ 115 МБ; ниже этого запаса начинать бессмысленно. */
        private const val MIN_FREE_BYTES = 500L * 1024 * 1024

        /** Короче — это промах по кнопке, а не заметка. */
        private const val MIN_USEFUL_SECONDS = 1.0

        fun start(context: Context, kind: RecordKind) {
            val i = Intent(context, RecorderService::class.java)
                .setAction(ACTION_START)
                .putExtra(EXTRA_KIND, kind.id)
            context.startForegroundService(i)
        }

        fun stop(context: Context) {
            context.startService(
                Intent(context, RecorderService::class.java).setAction(ACTION_STOP)
            )
        }

        /** «2026-07-31_081500» — как у iPhone-компаньона, секунды обязательны. */
        fun stamp(date: Date): String =
            SimpleDateFormat("yyyy-MM-dd_HHmmss", Locale.US).format(date)
    }
}
