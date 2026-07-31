package ai.charoite.companion

import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

/**
 * Один актуальный фоновый запрос.
 *
 * Новый запуск отменяет предыдущий, а его запоздавший finally не может
 * погасить индикатор уже следующего запроса.
 */
internal class LatestJob(private val scope: CoroutineScope) {
    private val generation = AtomicLong()
    private var job: Job? = null

    @Synchronized
    fun replace(
        onStart: () -> Unit = {},
        onFinish: () -> Unit = {},
        block: suspend () -> Unit,
    ) {
        val ownGeneration = generation.incrementAndGet()
        job?.cancel()
        onStart()

        val next = scope.launch(start = CoroutineStart.LAZY) {
            try {
                block()
            } finally {
                if (generation.get() == ownGeneration) onFinish()
            }
        }
        job = next
        next.start()
    }
}
