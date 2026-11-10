package net.syslikoff.healdragg.ui

import org.json.JSONArray
import org.json.JSONObject

/** Аккуратные обёртки над JSON: API отдаёт «сырые» словари, а UI любит типы. */

internal fun JSONObject.str(key: String): String? {
    if (!has(key) || isNull(key)) return null
    val value = optString(key, "").trim()
    return value.ifBlank { null }
}

internal fun JSONObject.num(key: String): Long =
    if (has(key) && !isNull(key)) optLong(key, 0L) else 0L

internal fun JSONObject.int(key: String): Int = num(key).toInt()

internal fun JSONObject.bool(key: String): Boolean = has(key) && !isNull(key) && optBoolean(key)

internal fun JSONObject.obj(key: String): JSONObject? =
    if (has(key) && !isNull(key)) optJSONObject(key) else null

internal fun JSONObject.arr(key: String): JSONArray =
    if (has(key) && !isNull(key)) optJSONArray(key) ?: JSONArray() else JSONArray()

data class TargetStats(
    val total: Int = 0,
    val admin: Int = 0,
    val left: Int = 0,
    val privateCount: Int = 0,
    val publicCount: Int = 0,
) {
    companion object {
        fun from(o: JSONObject?): TargetStats = if (o == null) TargetStats() else TargetStats(
            total = o.int("total"),
            admin = o.int("admin"),
            left = o.int("left_count"),
            privateCount = o.int("private"),
            publicCount = o.int("public"),
        )
    }
}

data class ActivityStats(
    val messages: Int = 0,
    val chats: Int = 0,
    val replies: Int = 0,
    val mentioned: Int = 0,
    val peers: Int = 0,
    val firstDate: Long = 0,
    val lastDate: Long = 0,
) {
    companion object {
        fun from(o: JSONObject?): ActivityStats = if (o == null) ActivityStats() else ActivityStats(
            messages = o.int("messages"),
            chats = o.int("chats"),
            replies = o.int("replies"),
            mentioned = o.int("mentioned"),
            peers = o.int("peers"),
            firstDate = o.num("first_date"),
            lastDate = o.num("last_date"),
        )
    }
}

data class Target(
    val tgId: Long,
    val username: String?,
    val firstName: String?,
    val lastName: String?,
    val bio: String?,
    val isBot: Boolean,
    val isDeleted: Boolean,
    val addedAt: Long,
    val lastScanAt: Long,
    val groupsCount: Int,
    val alertsUnread: Int,
    val stats: TargetStats,
    val activity: ActivityStats,
    val lastScan: String?,
) {
    val displayName: String
        get() {
            val full = listOfNotNull(firstName, lastName).joinToString(" ").trim()
            return full.ifBlank { username?.let { "@$it" } ?: "ID $tgId" }
        }

    val handle: String
        get() = username?.let { "@$it" } ?: tgId.toString()

    val initials: String
        get() = displayName
            .split(' ', '@')
            .filter { it.isNotBlank() }
            .take(2)
            .joinToString("") { it.first().uppercase() }
            .ifBlank { "?" }

    companion object {
        fun from(o: JSONObject): Target = Target(
            tgId = o.num("tg_user_id"),
            username = o.str("username"),
            firstName = o.str("first_name"),
            lastName = o.str("last_name"),
            bio = o.str("bio"),
            isBot = o.bool("is_bot"),
            isDeleted = o.bool("is_deleted"),
            addedAt = o.num("added_at"),
            lastScanAt = o.num("last_scan_at"),
            groupsCount = o.int("groups_count"),
            alertsUnread = o.int("alerts_unread"),
            stats = TargetStats.from(o.obj("stats")),
            activity = ActivityStats.from(o.obj("activity")),
            lastScan = o.str("last_scan"),
        )
    }
}

data class Chat(
    val tgId: Long,
    val title: String,
    val username: String?,
    val link: String?,
    val kind: String?,
    val isPublic: Boolean,
    val status: String?,
    val members: Int,
    val messagesTotal: Int,
    val lastMessageAt: Long,
    val source: String?,
) {
    companion object {
        fun from(o: JSONObject): Chat = Chat(
            tgId = o.num("tg_id"),
            title = o.str("title") ?: "без названия",
            username = o.str("username"),
            link = o.str("link"),
            kind = o.str("kind"),
            isPublic = o.bool("is_public"),
            status = o.str("status"),
            members = o.int("members_count"),
            messagesTotal = o.int("messages_total"),
            lastMessageAt = o.num("last_message_at"),
            source = o.str("source"),
        )
    }
}

data class ChatRow(
    val title: String,
    val tgId: Long,
    val username: String?,
    val link: String?,
    val status: String?,
)

data class LinkPage(
    val total: Int,
    val offset: Int,
    val hasMore: Boolean,
    val rows: List<ChatRow>,
)

data class Message(
    val id: Long,
    val groupTitle: String,
    val groupUsername: String?,
    val groupLink: String?,
    val text: String,
    val date: Long,
    val media: String?,
    val views: Int,
    val replies: Boolean,
) {
    companion object {
        fun from(o: JSONObject): Message = Message(
            id = o.num("id"),
            groupTitle = o.str("title") ?: "чат",
            groupUsername = o.str("username"),
            groupLink = o.str("link"),
            text = o.optString("text", "").trim(),
            date = o.num("date"),
            media = o.str("media"),
            views = o.int("views"),
            replies = o.str("reply_to_peer") != null,
        )
    }
}

data class AlertItem(
    val id: Long,
    val ts: Long,
    val kind: String,
    val title: String,
    val body: String?,
    val read: Boolean,
    val targetTgId: Long,
    val targetUsername: String?,
) {
    companion object {
        fun from(o: JSONObject): AlertItem = AlertItem(
            id = o.num("id"),
            ts = o.num("ts"),
            kind = o.str("kind") ?: "event",
            title = o.str("title") ?: "Событие",
            body = o.str("body"),
            read = o.bool("read"),
            targetTgId = o.num("tg_user_id"),
            targetUsername = o.str("username"),
        )
    }
}

data class Stats(
    val groups: Int = 0,
    val publicGroups: Int = 0,
    val activeGroups: Int = 0,
    val targets: Int = 0,
    val targetGroups: Int = 0,
    val sessions: Int = 0,
    val scans: Int = 0,
    val dbHuman: String = "—",
) {
    companion object {
        fun from(o: JSONObject): Stats = Stats(
            groups = o.int("groups"),
            publicGroups = o.int("public_groups"),
            activeGroups = o.int("active_groups"),
            targets = o.int("targets"),
            targetGroups = o.int("target_groups"),
            sessions = o.int("sessions"),
            scans = o.int("scans"),
            dbHuman = o.str("db_human") ?: "—",
        )
    }
}

data class GraphNode(
    val id: String,
    val type: String,
    val label: String,
    val tgId: Long,
    val weight: Int,
    val meta: JSONObject?,
) {
    val username: String? get() = meta?.str("username")
    val link: String? get() = meta?.str("link")
    val status: String? get() = meta?.str("status")
}

data class GraphEdge(val source: String, val target: String, val weight: Int, val kind: String)

data class GraphData(
    val targetName: String,
    val targetUsername: String?,
    val nodes: List<GraphNode>,
    val edges: List<GraphEdge>,
) {
    companion object {
        fun from(o: JSONObject): GraphData {
            val nodes = mutableListOf<GraphNode>()
            val nodesArr = o.arr("nodes")
            for (i in 0 until nodesArr.length()) {
                val n = nodesArr.optJSONObject(i) ?: continue
                nodes += GraphNode(
                    id = n.str("id") ?: "n$i",
                    type = n.str("type") ?: "chat",
                    label = n.str("label") ?: "—",
                    tgId = n.num("tg_id"),
                    weight = n.int("weight").coerceAtLeast(1),
                    meta = n.obj("meta"),
                )
            }
            val edges = mutableListOf<GraphEdge>()
            val edgesArr = o.arr("edges")
            for (i in 0 until edgesArr.length()) {
                val e = edgesArr.optJSONObject(i) ?: continue
                edges += GraphEdge(
                    source = e.str("source") ?: "target",
                    target = e.str("target") ?: continue,
                    weight = e.int("weight").coerceAtLeast(1),
                    kind = e.str("kind") ?: "chat",
                )
            }
            val t = o.obj("target")
            return GraphData(
                targetName = t?.str("name") ?: "Цель",
                targetUsername = t?.str("username"),
                nodes = nodes,
                edges = edges,
            )
        }
    }
}

data class Job(
    val key: String,
    val kind: String,
    val phase: String,
    val done: Int,
    val total: Int,
    val found: Int,
    val finished: Boolean,
    val error: String?,
    val progress: String?,
    val targetId: Long,
) {
    val percent: Float
        get() = if (total <= 0) 0f else (done.toFloat() / total).coerceIn(0f, 1f)

    companion object {
        fun from(o: JSONObject): Job {
            val state = o.obj("state")
            return Job(
                key = o.str("key") ?: "",
                kind = o.str("kind") ?: "",
                phase = state?.str("phase") ?: "…",
                done = state?.int("done") ?: 0,
                total = state?.int("total") ?: 0,
                found = state?.int("found") ?: 0,
                finished = o.bool("finished"),
                error = o.str("error"),
                progress = o.str("progress"),
                targetId = o.num("target_id"),
            )
        }
    }
}

/** Подключённый Telegram-аккаунт, с которого приложение читает чаты. */
data class SessionInfo(
    val name: String,
    val username: String?,
    val tgUserId: Long,
    val status: String,
    val groupsCount: Int,
    val lastUsedAt: Long,
) {
    val active: Boolean get() = status.equals("active", ignoreCase = true)

    val handle: String
        get() = username?.takeIf { it.isNotBlank() }?.let { "@$it" } ?: "id $tgUserId"

    companion object {
        fun from(o: JSONObject) = SessionInfo(
            name = o.str("name") ?: "аккаунт",
            username = o.str("username"),
            tgUserId = o.num("tg_user_id"),
            status = o.str("status") ?: "active",
            groupsCount = o.int("groups_count"),
            lastUsedAt = o.num("last_used_at"),
        )
    }
}

/** Ответ движка на шаг входа в Telegram. */
data class LoginFlow(
    val id: String,
    val status: String,
    val passwordNeeded: Boolean,
    val name: String?,
    val username: String?,
    val tgUserId: Long,
) {
    val done: Boolean get() = status == "done"

    companion object {
        fun from(o: JSONObject) = LoginFlow(
            id = o.str("id") ?: "",
            status = o.str("status") ?: "",
            passwordNeeded = o.bool("password_needed"),
            name = o.str("name"),
            username = o.str("username"),
            tgUserId = o.num("tg_user_id"),
        )
    }
}

/** Страница каталога публичных чатов из локальной базы. */
data class CatalogPage(
    val total: Int,
    val page: Int,
    val pages: Int,
    val rows: List<Chat>,
) {
    companion object {
        fun from(o: JSONObject): CatalogPage {
            val arr = o.arr("rows")
            val rows = (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }.map(Chat::from)
            return CatalogPage(
                total = o.int("total"),
                page = o.int("page").coerceAtLeast(1),
                pages = o.int("pages").coerceAtLeast(1),
                rows = rows,
            )
        }
    }
}
