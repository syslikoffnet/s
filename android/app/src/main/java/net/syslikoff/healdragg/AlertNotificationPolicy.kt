package net.syslikoff.healdragg

/** Курсор относится к конкретному источнику и потоку; при отказе он не сохраняется. */
internal object AlertNotificationPolicy {
    fun shouldNotify(
        unread: Int,
        latestUnreadId: Long,
        source: String,
        stream: String,
        savedSource: String?,
        savedStream: String?,
        savedId: Long,
    ): Boolean {
        val lastId = if (source == savedSource && stream == savedStream) savedId else 0L
        return unread > 0 && latestUnreadId > 0 && latestUnreadId > lastId
    }
}
