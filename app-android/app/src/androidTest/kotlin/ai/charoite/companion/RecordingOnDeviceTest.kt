package ai.charoite.companion

import android.Manifest
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.rule.GrantPermissionRule
import java.io.File
import java.io.RandomAccessFile
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Проверка на живом железе: микрофон → WAV → очередь.
 *
 * Юнит-тесты знают про заголовок, но не знают, слышит ли планшет комнату.
 * Здесь запись идёт через настоящий сервис и настоящий AudioRecord —
 * ровно то, что происходит на встрече.
 */
@RunWith(AndroidJUnit4::class)
class RecordingOnDeviceTest {

    @get:Rule
    val micPermission: GrantPermissionRule =
        GrantPermissionRule.grant(Manifest.permission.RECORD_AUDIO)

    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    @Before
    fun clean() {
        Outbox.dir(context).listFiles()?.forEach { if (it.isFile) it.delete() }
        Outbox.inProgress(context).listFiles()?.forEach { it.delete() }
    }

    @Test
    fun пишет_валидный_wav_и_кладёт_в_очередь() {
        RecorderService.start(context, RecordKind.NOTE)
        Thread.sleep(5_000)
        assertTrue("сервис не начал писать", RecorderState.recording.value)

        RecorderService.stop(context)
        // Стоп проходит через главный поток сервиса и досылку — даём время.
        waitFor { !RecorderState.recording.value }
        waitFor { queued().isNotEmpty() }

        val files = queued()
        assertEquals("ожидали ровно один файл в очереди", 1, files.size)
        val wav = files.first()

        // Имя: заметка получает префикс, по которому Mac выбирает конвейер.
        assertTrue("имя без префикса note_: ${wav.name}", wav.name.startsWith("note_android_"))
        assertTrue("не .wav: ${wav.name}", wav.name.endsWith(".wav"))

        // 5 секунд при 32 КБ/с — не меньше 3 секунд с запасом на старт микрофона.
        val dataBytes = wav.length() - WavWriter.HEADER_SIZE
        assertTrue("слишком мало данных: $dataBytes байт", dataBytes > 3 * 32_000)

        RandomAccessFile(wav, "r").use { raf ->
            val head = ByteArray(4)
            raf.readFully(head)
            assertEquals("RIFF", String(head, Charsets.US_ASCII))
            raf.seek(24)
            assertEquals("частота не 16 кГц", 16_000L, le32(raf))
            raf.seek(40)
            assertEquals("длина данных в заголовке разошлась с файлом", dataBytes, le32(raf))
        }
    }

    private fun queued(): List<File> =
        Outbox.dir(context).listFiles()?.filter { it.isFile && it.extension == "wav" } ?: emptyList()

    private fun waitFor(timeoutMs: Long = 10_000, condition: () -> Boolean) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (condition()) return
            Thread.sleep(200)
        }
    }

    private fun le32(raf: RandomAccessFile): Long {
        val b = ByteArray(4)
        raf.readFully(b)
        return (b[0].toLong() and 0xff) or ((b[1].toLong() and 0xff) shl 8) or
            ((b[2].toLong() and 0xff) shl 16) or ((b[3].toLong() and 0xff) shl 24)
    }
}
