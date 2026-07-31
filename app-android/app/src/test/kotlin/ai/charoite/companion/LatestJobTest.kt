package ai.charoite.companion

import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancel
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LatestJobTest {

    @Test(timeout = 2_000)
    fun `новый запрос отменяет старый и завершает только свой индикатор`() {
        val dispatcher = Executors.newSingleThreadExecutor().asCoroutineDispatcher()
        val scope = CoroutineScope(SupervisorJob() + dispatcher)
        val firstStarted = CountDownLatch(1)
        val firstCancelled = CountDownLatch(1)
        val secondFinished = CountDownLatch(1)
        val staleFinishCalled = AtomicBoolean(false)
        val runner = LatestJob(scope)

        try {
            runner.replace(onFinish = { staleFinishCalled.set(true) }) {
                firstStarted.countDown()
                try {
                    awaitCancellation()
                } finally {
                    firstCancelled.countDown()
                }
            }
            assertTrue(firstStarted.await(1, TimeUnit.SECONDS))

            runner.replace(onFinish = { secondFinished.countDown() }) { }

            assertTrue(firstCancelled.await(1, TimeUnit.SECONDS))
            assertTrue(secondFinished.await(1, TimeUnit.SECONDS))
            assertFalse(staleFinishCalled.get())
        } finally {
            scope.cancel()
            dispatcher.close()
        }
    }
}
