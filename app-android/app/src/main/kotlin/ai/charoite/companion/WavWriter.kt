package ai.charoite.companion

import java.io.File
import java.io.RandomAccessFile

/**
 * Запись PCM прямо в WAV.
 *
 * Почему не MediaRecorder с M4A: контейнер MPEG-4 без штатного stop()
 * остаётся без атома `moov` и не читается ничем — ровно та беда, из-за
 * которой iPhone-компаньон пишет CAF. Час чужой встречи не переснять,
 * поэтому формат обязан переживать смерть процесса.
 *
 * Почему 16 кГц моно 16 бит: это ровно то, что нужно распознаванию на Mac
 * (`transcribe_file.to_wav16k`). Пишем сразу в целевом формате — ни одного
 * перекодирования по дороге. Цена — 32 КБ/с, около 115 МБ за час.
 *
 * Заголовок обновляется по ходу записи (раз в [HEADER_REFRESH_BYTES]),
 * поэтому даже убитый системой файл остаётся валидным WAV: теряется
 * не звук, а лишь объявленная длина последних секунд. Дописать её до
 * фактической умеет [repair].
 */
class WavWriter(
    file: File,
    private val sampleRate: Int = SAMPLE_RATE,
    private val channels: Int = 1,
    private val bitsPerSample: Int = 16,
) : AutoCloseable {

    private val raf = RandomAccessFile(file, "rw")
    private var dataBytes = 0L
    private var sinceHeaderRefresh = 0L

    init {
        raf.setLength(0)
        raf.write(header(0))
    }

    fun write(buffer: ByteArray, length: Int) {
        if (length <= 0) return
        raf.write(buffer, 0, length)
        dataBytes += length
        sinceHeaderRefresh += length
        if (sinceHeaderRefresh >= HEADER_REFRESH_BYTES) {
            refreshHeader()
            sinceHeaderRefresh = 0
        }
    }

    /** Секунды записанного — для таймера и для проверки, что файл не пустой. */
    fun seconds(): Double = dataBytes.toDouble() / (sampleRate * channels * bitsPerSample / 8)

    override fun close() {
        try {
            refreshHeader()
            raf.fd.sync()
        } finally {
            raf.close()
        }
    }

    private fun refreshHeader() {
        val pos = raf.filePointer
        raf.seek(0)
        raf.write(header(dataBytes))
        raf.seek(pos)
    }

    private fun header(dataLen: Long): ByteArray {
        val byteRate = sampleRate * channels * bitsPerSample / 8
        val blockAlign = channels * bitsPerSample / 8
        val out = ByteArray(HEADER_SIZE)
        var i = 0
        fun ascii(s: String) { for (c in s) out[i++] = c.code.toByte() }
        fun le32(v: Long) {
            out[i++] = (v and 0xff).toByte()
            out[i++] = ((v shr 8) and 0xff).toByte()
            out[i++] = ((v shr 16) and 0xff).toByte()
            out[i++] = ((v shr 24) and 0xff).toByte()
        }
        fun le16(v: Int) {
            out[i++] = (v and 0xff).toByte()
            out[i++] = ((v shr 8) and 0xff).toByte()
        }
        ascii("RIFF")
        le32(36 + dataLen)
        ascii("WAVE")
        ascii("fmt ")
        le32(16)              // размер PCM-подчанка
        le16(1)               // формат: PCM
        le16(channels)
        le32(sampleRate.toLong())
        le32(byteRate.toLong())
        le16(blockAlign)
        le16(bitsPerSample)
        ascii("data")
        le32(dataLen)
        return out
    }

    companion object {
        const val SAMPLE_RATE = 16_000
        const val HEADER_SIZE = 44

        /** Раз в ~5 секунд звука (16 кГц моно 16 бит ≈ 32 КБ/с). */
        private const val HEADER_REFRESH_BYTES = 160_000L

        /**
         * Файл, переживший смерть процесса: в заголовке длина на несколько
         * секунд меньше фактической. Дописываем её по реальному размеру.
         *
         * Возвращает true, если файл похож на WAV и починен (или уже был цел).
         */
        fun repair(file: File): Boolean {
            if (!file.isFile || file.length() <= HEADER_SIZE) return false
            RandomAccessFile(file, "rw").use { raf ->
                val magic = ByteArray(4)
                raf.readFully(magic)
                if (String(magic, Charsets.US_ASCII) != "RIFF") return false
                val dataLen = file.length() - HEADER_SIZE
                raf.seek(4)
                writeLe32(raf, 36 + dataLen)
                raf.seek(40)
                writeLe32(raf, dataLen)
            }
            return true
        }

        private fun writeLe32(raf: RandomAccessFile, v: Long) {
            raf.write(
                byteArrayOf(
                    (v and 0xff).toByte(),
                    ((v shr 8) and 0xff).toByte(),
                    ((v shr 16) and 0xff).toByte(),
                    ((v shr 24) and 0xff).toByte(),
                )
            )
        }
    }
}
