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

internal fun JSONObject.dbl(key: String): Double =
    if (has(key) && !isNull(key)) optDouble(key, 0.0) else 0.0

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
    val photoPresent: Boolean = false,
    val photoUpdatedAt: Long = 0,
    val gender: String? = null,
    val genderConfidence: Double = 0.0,
    val country: String? = null,
    val countryName: String? = null,
    val countryConfidence: Double = 0.0,
    val giftsCount: Int = 0,
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
            photoPresent = o.obj("photo")?.bool("present") == true,
            photoUpdatedAt = o.obj("photo")?.num("updated_at") ?: 0L,
            gender = o.obj("intel")?.str("gender"),
            genderConfidence = o.obj("intel")?.dbl("gender_confidence") ?: 0.0,
            country = o.obj("intel")?.str("country"),
            countryName = o.obj("intel")?.str("country_name"),
            countryConfidence = o.obj("intel")?.dbl("country_confidence") ?: 0.0,
            giftsCount = o.obj("gifts")?.int("count") ?: 0,
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
            source = o.str("source") ?: o.str("discovered_from"),
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
    val state: JSONObject? = null,
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
                state = state,
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

/** Подарок из профиля Telegram (обычный или NFT). */
data class GiftItem(
    val id: Long,
    val ownerTgId: Long,
    val ownerUsername: String?,
    val ownerName: String?,
    val title: String,
    val kind: String,
    val stars: Int,
    val fromTgId: Long,
    val fromUsername: String?,
    val fromName: String?,
    val message: String?,
    val date: Long,
    val isNft: Boolean,
    val nameHidden: Boolean,
    val slug: String? = null,
    val num: Int = 0,
    val valueAmount: Long = 0,
    val valueCurrency: String? = null,
    val resellAmount: Long = 0,
    val availabilityIssued: Int = 0,
    val availabilityTotal: Int = 0,
    val onChain: Boolean = false,
    val giftAddress: String? = null,
    val ownerAddress: String? = null,
    val valueUsd: Long = 0,
    val burned: Boolean = false,
) {
    val ownerHandle: String
        get() = ownerUsername?.let { "@$it" } ?: "id $ownerTgId"

    val nftLink: String?
        get() = slug?.takeIf { it.isNotBlank() }?.let { "https://t.me/nft/$it" }

    val priceLabel: String?
        get() = Money.both(valueAmount, valueCurrency ?: "TON")

    companion object {
        fun from(o: JSONObject): GiftItem = GiftItem(
            id = o.num("id"),
            ownerTgId = o.num("owner_tg_id"),
            ownerUsername = o.str("owner_username"),
            ownerName = o.str("owner_name"),
            title = o.str("title") ?: "Подарок",
            kind = o.str("kind") ?: "regular",
            stars = o.int("stars"),
            fromTgId = o.num("from_tg_id"),
            fromUsername = o.str("from_username"),
            fromName = o.str("from_name"),
            message = o.str("message"),
            date = o.num("date"),
            isNft = o.bool("is_nft"),
            nameHidden = o.bool("name_hidden"),
            slug = o.str("slug"),
            num = o.int("num"),
            valueAmount = o.num("value_amount"),
            valueCurrency = o.str("value_currency"),
            resellAmount = o.num("resell_amount"),
            availabilityIssued = o.int("availability_issued"),
            availabilityTotal = o.int("availability_total"),
            onChain = o.optInt("on_chain") == 1,
            giftAddress = o.str("gift_address"),
            ownerAddress = o.str("owner_address"),
            valueUsd = o.num("value_usd"),
            burned = o.optInt("burned") == 1,
        )
    }
}

data class GiftSummary(
    val total: Int = 0,
    val nft: Int = 0,
    val owners: Int = 0,
    val senders: Int = 0,
    val nftChain: Int = 0,
    val nftHosted: Int = 0,
    val inventory: Int = 0,
    val inventoryChain: Int = 0,
    val inventoryHosted: Int = 0,
) {
    companion object {
        fun from(o: JSONObject?): GiftSummary = if (o == null) GiftSummary() else GiftSummary(
            total = o.int("total"),
            nft = o.int("nft"),
            owners = o.int("owners"),
            senders = o.int("senders"),
            nftChain = o.int("nft_chain"),
            nftHosted = o.int("nft_hosted"),
            inventory = o.int("inventory"),
            inventoryChain = o.int("inventory_chain"),
            inventoryHosted = o.int("inventory_hosted"),
        )
    }
}

/** Сводка NFT-инвентаря: что уже собрано по коллекциям и где лежит. */
data class NftSummary(
    val total: Int = 0,
    val onChain: Int = 0,
    val hosted: Int = 0,
    val owners: Int = 0,
    val burned: Int = 0,
    val collections: Int = 0,
    val capacity: Int = 0,
    val scanned: Int = 0,
) {
    companion object {
        fun from(o: JSONObject?): NftSummary = if (o == null) NftSummary() else NftSummary(
            total = o.int("total"),
            onChain = o.int("on_chain"),
            hosted = o.int("hosted"),
            owners = o.int("owners"),
            burned = o.int("burned"),
            collections = o.int("collections"),
            capacity = o.int("capacity"),
            scanned = o.int("scanned"),
        )
    }
}

/** Уникальный подарок из инвентаря (одна вещь = один slug). */
data class NftItem(
    val slug: String,
    val giftId: Long,
    val num: Int,
    val title: String,
    val ownerTgId: Long,
    val ownerUsername: String?,
    val ownerName: String?,
    val ownerAddress: String?,
    val giftAddress: String?,
    val onChain: Boolean,
    val burned: Boolean,
    val valueAmount: Long,
    val valueCurrency: String?,
    val valueUsd: Long,
    val resellAmount: Long,
    val availabilityIssued: Int,
    val availabilityTotal: Int,
    val seenAt: Long,
) {
    val nftLink: String get() = "https://t.me/nft/$slug"
    val ownerHandle: String
        get() = ownerUsername?.let { "@$it" } ?: ownerName ?: "id $ownerTgId"
    val priceLabel: String?
        get() = when {
            resellAmount > 0 -> Money.both(resellAmount, "STARS")?.let { "$it · ресейл" }
            valueAmount > 0 -> Money.both(valueAmount, valueCurrency ?: "TON")
            else -> null
        }
    val chainLabel: String
        get() = when {
            burned -> "сожжён"
            onChain -> "на кошельке"
            else -> "в Telegram"
        }

    companion object {
        fun from(o: JSONObject): NftItem = NftItem(
            slug = o.str("slug").orEmpty(),
            giftId = o.num("gift_id"),
            num = o.int("num"),
            title = o.str("title") ?: "NFT",
            ownerTgId = o.num("owner_tg_id"),
            ownerUsername = o.str("owner_username"),
            ownerName = o.str("owner_name"),
            ownerAddress = o.str("owner_address"),
            giftAddress = o.str("gift_address"),
            onChain = o.optInt("on_chain") == 1,
            burned = o.optInt("burned") == 1,
            valueAmount = o.num("value_amount"),
            valueCurrency = o.str("value_currency"),
            valueUsd = o.num("value_usd"),
            resellAmount = o.num("resell_amount"),
            availabilityIssued = o.int("availability_issued"),
            availabilityTotal = o.int("availability_total"),
            seenAt = o.num("seen_at"),
        )
    }
}

/** Владелец NFT: почти любой пользователь, встреченный при обходе сети. */
data class NftOwner(
    val tgId: Long,
    val username: String?,
    val name: String?,
    val nft: Int,
    val onChain: Int,
    val hosted: Int,
    val seenAt: Long,
) {
    val handle: String
        get() = username?.let { "@$it" } ?: name ?: "id $tgId"

    companion object {
        fun from(o: JSONObject): NftOwner = NftOwner(
            tgId = o.num("owner_tg_id"),
            username = o.str("owner_username"),
            name = o.str("owner_name"),
            nft = o.int("nft"),
            onChain = o.int("on_chain"),
            hosted = o.int("hosted"),
            seenAt = o.num("seen_at"),
        )
    }
}

data class NftCollection(
    val slugBase: String,
    val title: String?,
    val total: Int,
    val cursor: Int,
    val scanned: Int,
    val owners: Int,
    val updatedAt: Long,
) {
    val percent: Float
        get() = if (total <= 0) 0f else (scanned.coerceAtMost(total).toFloat() / total)

    companion object {
        fun from(o: JSONObject): NftCollection = NftCollection(
            slugBase = o.str("slug_base").orEmpty(),
            title = o.str("title"),
            total = o.int("total"),
            cursor = o.int("cursor"),
            scanned = o.int("scanned"),
            owners = o.int("owners"),
            updatedAt = o.num("updated_at"),
        )
    }
}

data class NftPage(
    val rows: List<NftItem>,
    val summary: NftSummary,
    val owners: List<NftOwner>,
    val collections: List<NftCollection>,
    /** Сколько всего строк подходит под фильтр (не только загруженная страница). */
    val total: Int = 0,
)

data class GiftOwner(
    val tgId: Long,
    val gifts: Int,
    val nft: Int,
    val lastDate: Long,
    val username: String? = null,
    val name: String? = null,
    val onChain: Int = 0,
    val hosted: Int = 0,
)

data class GiftPage(
    val rows: List<GiftItem>,
    val summary: GiftSummary,
    val owners: List<GiftOwner>,
)

data class PhotoData(val base64: String, val updatedAt: Long)

/** Разбор вставленного списка ссылок — без похода в Telegram. */
data class ImportItem(
    val raw: String,
    val kind: String,
    val value: String,
    val link: String?,
)

data class ImportIssue(val raw: String, val reason: String?)

data class ImportStats(
    val total: Int = 0,
    val valid: Int = 0,
    val invalid: Int = 0,
    val duplicates: Int = 0,
    val invalidPercent: Double = 0.0,
    val usernames: Int = 0,
    val invites: Int = 0,
    val ids: Int = 0,
)

data class ImportPreview(
    val items: List<ImportItem>,
    val invalid: List<ImportIssue>,
    val stats: ImportStats,
    val sessions: List<String>,
)

fun importStatsFrom(o: JSONObject?): ImportStats = if (o == null) ImportStats() else ImportStats(
    total = o.int("total"),
    valid = o.int("valid"),
    invalid = o.int("invalid"),
    duplicates = o.int("duplicates"),
    invalidPercent = o.dbl("invalid_percent"),
    usernames = o.int("usernames"),
    invites = o.int("invites"),
    ids = o.int("ids"),
)

/** Итог импорта: сводка и построчные статусы из состояния job. */
data class ImportOutcome(
    val added: Int,
    val existing: Int,
    val joined: Int,
    val requested: Int,
    val needJoin: Int,
    val private: Int,
    val channels: Int,
    val groups: Int,
    val invalid: Int,
    val errors: Int,
    val invalidPercent: Double,
    val rows: List<ImportRow>,
)

data class ImportRow(
    val raw: String,
    val status: String,
    val title: String?,
    val username: String?,
    val reason: String?,
)

fun importOutcomeFrom(state: JSONObject?): ImportOutcome? {
    val summary = state?.obj("summary") ?: return null
    val rows = jsonObjects(state.arr("rows")).map {
        ImportRow(
            raw = it.str("raw").orEmpty(),
            status = it.str("status").orEmpty(),
            title = it.str("title"),
            username = it.str("username"),
            reason = it.str("reason"),
        )
    }
    return ImportOutcome(
        added = summary.int("added"),
        existing = summary.int("existing"),
        joined = summary.int("joined"),
        requested = summary.int("requested"),
        needJoin = summary.int("need_join"),
        private = summary.int("private"),
        channels = summary.int("channels"),
        groups = summary.int("groups"),
        invalid = summary.int("invalid"),
        errors = summary.int("errors"),
        invalidPercent = summary.dbl("invalid_percent"),
        rows = rows,
    )
}


/** Результат импорта готовой сессии/tdata. */
data class ImportedSession(
    val name: String,
    val tgUserId: Long,
    val dcId: Int,
    val source: String?,
)

data class ImportError(val file: String, val error: String)

data class SessionImportResult(
    val imported: List<ImportedSession>,
    val errors: List<ImportError>,
) {
    val summary: String
        get() = when {
            imported.isEmpty() && errors.isEmpty() -> "Ничего не импортировано"
            errors.isEmpty() -> "Добавлено аккаунтов: ${imported.size}"
            imported.isEmpty() -> "Не удалось: ${errors.first().error}"
            else -> "Добавлено ${imported.size}, ошибок ${errors.size}"
        }
}

/** Резервная копия: база + сессии в одном zip. */
data class BackupInfo(
    val name: String,
    val size: Long,
    val createdAt: Long,
) {
    val sizeLabel: String get() = humanSize(size)

    companion object {
        fun from(o: JSONObject): BackupInfo = BackupInfo(
            name = o.str("name").orEmpty(),
            size = o.num("size"),
            createdAt = o.num("created_at"),
        )
    }
}

data class BackupPage(
    val rows: List<BackupInfo>,
    val latest: BackupInfo?,
    val counts: JSONObject?,
)

/** Узел общей карты: цель, чат или собеседник. */
data class MapNode(
    val id: String,
    val type: String,
    val label: String,
    val tgId: Long,
    val weight: Int,
    val username: String?,
) {
    val kindLabel: String
        get() = when (type) {
            "target" -> "цель"
            "chat" -> "чат"
            "peer" -> "собеседник"
            else -> type
        }

    companion object {
        fun from(o: org.json.JSONObject): MapNode = MapNode(
            id = o.str("id") ?: "",
            type = o.str("type") ?: "peer",
            label = o.str("label") ?: "без названия",
            tgId = o.num("tg_id"),
            weight = o.num("weight").toInt(),
            username = o.obj("meta")?.str("username"),
        )
    }
}

/** Связь между узлами: «пишет в чат», «общается с», «отвечает в чате». */
data class MapLink(
    val source: String,
    val target: String,
    val kind: String,
    val weight: Int,
)

data class GlobalMap(
    val nodes: List<MapNode>,
    val links: List<MapLink>,
    val targets: Int,
    val chats: Int,
    val peers: Int,
)
