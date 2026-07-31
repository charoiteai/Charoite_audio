package ai.charoite.companion

import java.io.File
import java.nio.file.Files
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DeliverySafetyTest {

    @Test
    fun `активная запись никогда не считается сиротой`() {
        val dir = Files.createTempDirectory("charoite-current").toFile()
        val active = File(dir, "android_meeting.wav").apply { writeBytes(ByteArray(100)) }

        assertEquals(
            emptyList<File>(),
            rescuableFiles(dir, recording = true, audioExtensions = setOf("wav", "m4a")),
        )
        assertTrue(active.exists())
        dir.deleteRecursively()
    }

    @Test
    fun `восстановление берёт только файлы записи`() {
        val dir = Files.createTempDirectory("charoite-current").toFile()
        val wav = File(dir, "b.WAV").apply { writeBytes(ByteArray(100)) }
        val m4a = File(dir, "a.m4a").apply { writeBytes(ByteArray(100)) }
        File(dir, "note.txt").writeText("не запись")
        File(dir, "folder.wav").mkdir()

        assertEquals(
            listOf(m4a, wav),
            rescuableFiles(dir, recording = false, audioExtensions = setOf("wav", "m4a")),
        )
        dir.deleteRecursively()
    }

    @Test
    fun `повреждённый wav не готов к очереди`() {
        val dir = Files.createTempDirectory("charoite-current").toFile()
        val broken = File(dir, "broken.wav").apply { writeBytes(ByteArray(100) { 7 }) }
        val legacy = File(dir, "legacy.m4a").apply { writeBytes(ByteArray(100) { 7 }) }

        assertFalse(orphanReadyForQueue(broken))
        assertTrue(orphanReadyForQueue(legacy))
        dir.deleteRecursively()
    }

    @Test
    fun `оборванная публикация удаляет созданный ресурс`() {
        var cleaned = false

        val failure = runCatching {
            cleanupOnFailure(cleanup = { cleaned = true }) {
                error("copy failed")
            }
        }.exceptionOrNull()

        assertTrue(cleaned)
        assertEquals("copy failed", failure?.message)
    }

    @Test
    fun `успешная публикация не удаляет ресурс`() {
        var cleaned = false

        val result = cleanupOnFailure(cleanup = { cleaned = true }) { "published" }

        assertEquals("published", result)
        assertFalse(cleaned)
    }
}
