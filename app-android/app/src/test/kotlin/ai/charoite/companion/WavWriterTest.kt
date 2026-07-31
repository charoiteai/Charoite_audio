package ai.charoite.companion

import java.io.File
import java.io.RandomAccessFile
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Заголовок WAV — единственное, что отделяет час записи от «файл повреждён».
 * Проверяем то, ради чего формат и выбран: он читается и после обрыва.
 */
class WavWriterTest {

    private fun le32(f: File, at: Int): Long =
        RandomAccessFile(f, "r").use { raf ->
            raf.seek(at.toLong())
            val b = ByteArray(4)
            raf.readFully(b)
            (b[0].toLong() and 0xff) or ((b[1].toLong() and 0xff) shl 8) or
                ((b[2].toLong() and 0xff) shl 16) or ((b[3].toLong() and 0xff) shl 24)
        }

    private fun ascii(f: File, at: Int, len: Int): String =
        RandomAccessFile(f, "r").use { raf ->
            raf.seek(at.toLong())
            val b = ByteArray(len)
            raf.readFully(b)
            String(b, Charsets.US_ASCII)
        }

    @Test
    fun `заголовок описывает 16 кГц моно 16 бит`() {
        val f = File.createTempFile("rec", ".wav")
        WavWriter(f).use { it.write(ByteArray(3_200), 3_200) }

        assertEquals("RIFF", ascii(f, 0, 4))
        assertEquals("WAVE", ascii(f, 8, 4))
        assertEquals("data", ascii(f, 36, 4))
        assertEquals(16_000L, le32(f, 24))          // частота
        assertEquals(32_000L, le32(f, 28))          // байт в секунду
        assertEquals(3_200L, le32(f, 40))           // размер данных
        assertEquals(36 + 3_200L, le32(f, 4))       // размер RIFF
        f.delete()
    }

    @Test
    fun `секунды считаются по записанным байтам`() {
        val f = File.createTempFile("rec", ".wav")
        WavWriter(f).use { w ->
            w.write(ByteArray(32_000), 32_000)      // ровно секунда
            assertEquals(1.0, w.seconds(), 1e-9)
        }
        f.delete()
    }

    @Test
    fun `починка дописывает длину после обрыва`() {
        val f = File.createTempFile("rec", ".wav")
        // Имитируем уже умерший процесс: данные на диске есть, блокировка
        // отпущена, но сохранённый заголовок отстаёт от факта.
        WavWriter(f).use { it.write(ByteArray(8_000), 8_000) }
        RandomAccessFile(f, "rw").use { raf ->
            raf.seek(4)
            raf.write(ByteArray(4))
            raf.seek(40)
            raf.write(ByteArray(4))
        }

        assertEquals(0L, le32(f, 40))
        assertTrue(WavWriter.repair(f))
        assertEquals(8_000L, le32(f, 40))
        assertEquals(36 + 8_000L, le32(f, 4))
        f.delete()
    }

    @Test
    fun `активная запись не чинится как сирота`() {
        val f = File.createTempFile("active", ".wav")
        val w = WavWriter(f)
        try {
            w.write(ByteArray(8_000), 8_000)

            assertFalse(WavWriter.repair(f))
        } finally {
            w.close()
        }
        assertTrue(WavWriter.repair(f))
        f.delete()
    }

    @Test
    fun `чужой файл не чинится`() {
        val f = File.createTempFile("junk", ".wav")
        f.writeBytes(ByteArray(100) { 7 })
        assertFalse(WavWriter.repair(f))
        f.delete()
    }

    @Test
    fun `чужой RIFF контейнер не принимается за WAV`() {
        val f = File.createTempFile("riff", ".wav")
        f.writeBytes("RIFF".toByteArray() + ByteArray(96))
        assertFalse(WavWriter.repair(f))
        f.delete()
    }

    @Test
    fun `пустой файл не чинится`() {
        val f = File.createTempFile("empty", ".wav")
        assertFalse(WavWriter.repair(f))
        f.delete()
    }

    @Test
    fun `недоступная блокировка не срывает запись`() {
        // Блокировка — страховка от «сироты», а не условие записи. Если файловые
        // замки недоступны (чужой канал, экзотическая ФС прошивки), встреча всё
        // равно обязана писаться: потерять час разговора из-за страховки хуже,
        // чем остаться без страховки.
        val f = File.createTempFile("locked", ".wav")
        RandomAccessFile(f, "rw").use { holder ->
            holder.channel.lock().use {
                WavWriter(f).use { w -> w.write(ByteArray(3_200), 3_200) }
            }
        }
        assertEquals(3_200L, le32(f, 40))
        f.delete()
    }
}
