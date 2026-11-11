package net.syslikoff.healdragg

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AlertNotificationPolicyTest {
    private fun notify(unread: Int, latest: Long, last: Long, source: String = "a", stream: String = "one",
                       savedSource: String? = "a", savedStream: String? = "one") =
        AlertNotificationPolicy.shouldNotify(unread, latest, source, stream, savedSource, savedStream, last)

    @Test fun newAlertWithUnchangedUnreadCountIsNotLost() {
        assertTrue(notify(3, 42, 41))
        assertFalse(notify(3, 42, 42))
    }

    @Test fun readingAlertsDoesNotRenotifyOlderOnes() {
        assertFalse(notify(2, 40, 42))
        assertFalse(notify(0, 0, 42))
        assertTrue(notify(1, 43, 42))
    }

    @Test fun differentSourceOrRestoredStreamResetsCursor() {
        assertTrue(notify(1, 1, 42, source = "b"))
        assertTrue(notify(1, 1, 42, stream = "restored"))
        assertTrue(notify(1, 1, 0, savedSource = null, savedStream = null))
    }

    @Test fun invalidAndEmptyResultsDoNotAdvance() {
        assertFalse(notify(1, 0, 0))
        assertFalse(notify(-1, 1, 0))
    }
}
