package ai.charoite.companion

import java.io.File

/**
 * Кандидаты на восстановление после смерти процесса.
 *
 * Пока запись идёт, current/ принадлежит foreground-сервису целиком:
 * Activity может пересоздаться и не имеет права объявлять его файлы сиротами.
 */
internal fun rescuableFiles(
    current: File,
    recording: Boolean,
    audioExtensions: Set<String>,
): List<File> {
    if (recording) return emptyList()
    return current.listFiles()
        ?.filter { it.isFile && it.extension.lowercase() in audioExtensions }
        ?.sortedBy { it.name }
        .orEmpty()
}

/** WAV переносится в очередь только после успешной проверки/починки. */
internal fun orphanReadyForQueue(file: File): Boolean =
    file.extension.lowercase() != "wav" || WavWriter.repair(file)

/**
 * Ресурс, созданный для публикации, не должен пережить оборванную запись.
 *
 * Особенно важно для SAF-провайдера без rename: там ресурс уже носит
 * конечное имя `.wav`, и синхронизатор немедленно покажет его Mac.
 */
internal inline fun <T> cleanupOnFailure(cleanup: () -> Unit, block: () -> T): T =
    try {
        block()
    } catch (failure: Throwable) {
        cleanup()
        throw failure
    }
