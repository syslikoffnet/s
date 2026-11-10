package net.syslikoff.healdragg.ui

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

class ApiException(val code: Int, override val message: String) : Exception(message)

/**
 * Тонкий клиент REST-панели: те же эндпоинты, что у веба, только без браузера.
 * Движок локальный, поэтому таймауты короткие и ошибки показываются сразу.
 */
class Api(private val baseUrl: String, private val token: String) {

    private val root: String = if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/"

    suspend fun health(): Boolean = try {
        request("api/health").isNotEmpty()
    } catch (_: Exception) {
        false
    }

    suspend fun targets(): List<Target> = jsonObjects(array("api/targets")).map(Target::from)

    suspend fun stats(): Stats = Stats.from(obj("api/stats"))

    suspend fun alerts(unreadOnly: Boolean = false, limit: Int = 100): Pair<List<AlertItem>, Int> {
        val data = obj("api/alerts?limit=$limit${if (unreadOnly) "&unread=true" else ""}")
        val rows = data.arr("rows")
        val list = (0 until rows.length()).mapNotNull { rows.optJSONObject(it) }.map(AlertItem::from)
        return list to data.int("unread")
    }

    suspend fun markAlertsRead(ids: List<Long>? = null): Int {
        val body = JSONObject()
        if (ids != null) body.put("ids", JSONArray(ids))
        val data = obj("api/alerts/read", "POST", body)
        return data.int("unread")
    }

    suspend fun sessions(): List<SessionInfo> = jsonObjects(array("api/sessions")).map(SessionInfo::from)

    suspend fun startLogin(phone: String, name: String = "main"): LoginFlow {
        val body = JSONObject().put("phone", phone).put("name", name)
        return LoginFlow.from(obj("api/session/start", "POST", body, LOGIN_TIMEOUT))
    }

    suspend fun submitLoginCode(id: String, code: String): LoginFlow {
        val body = JSONObject().put("id", id).put("code", code)
        return LoginFlow.from(obj("api/session/code", "POST", body, LOGIN_TIMEOUT))
    }

    suspend fun submitLoginPassword(id: String, password: String): LoginFlow {
        val body = JSONObject().put("id", id).put("password", password)
        return LoginFlow.from(obj("api/session/password", "POST", body, LOGIN_TIMEOUT))
    }

    suspend fun cancelLogin(id: String) {
        request("api/session/$id/cancel", "POST", JSONObject())
    }

    suspend fun deleteSession(name: String) {
        request("api/sessions/${URLEncoder.encode(name, "UTF-8")}", "DELETE")
    }

    suspend fun target(tgId: Long): Target = Target.from(obj("api/targets/$tgId"))

    suspend fun addTarget(query: String): Target {
        val body = JSONObject().put("query", query)
        return Target.from(obj("api/targets", "POST", body))
    }

    suspend fun deleteTarget(tgId: Long) {
        request("api/targets/$tgId", "DELETE")
    }

    suspend fun chats(tgId: Long, page: Int, size: Int = 20, order: String = "last"): List<Chat> {
        val data = obj("api/targets/$tgId/groups?page=$page&size=$size&order=$order")
        val rows = data.arr("rows")
        return (0 until rows.length()).mapNotNull { rows.optJSONObject(it) }.map(Chat::from)
    }

    suspend fun links(tgId: Long, offset: Int, limit: Int = 100): LinkPage {
        val data = obj("api/targets/$tgId/links?offset=$offset&limit=$limit")
        val rows = data.arr("rows")
        return LinkPage(
            total = data.int("total"),
            offset = data.int("offset"),
            hasMore = data.bool("has_more"),
            rows = (0 until rows.length()).mapNotNull { rows.optJSONObject(it) }.map {
                ChatRow(
                    title = it.str("title") ?: "без названия",
                    tgId = it.num("tg_id"),
                    username = it.str("username"),
                    link = it.str("link"),
                    status = it.str("status"),
                )
            },
        )
    }

    suspend fun activity(tgId: Long, limit: Int = 100): Pair<ActivityStats, List<Message>> {
        val data = obj("api/targets/$tgId/activity?limit=$limit")
        val rows = data.arr("rows")
        return ActivityStats.from(data.obj("stats")) to
            (0 until rows.length()).mapNotNull { rows.optJSONObject(it) }.map(Message::from)
    }

    /** Режим наблюдения: сервер сам добирает свежие сообщения цели. */
    suspend fun liveActivity(tgId: Long, limit: Int = 120): Pair<ActivityStats, List<Message>> {
        val data = obj("api/targets/$tgId/activity/live?limit=$limit", "POST", null, LIVE_TIMEOUT)
        val rows = data.arr("rows")
        return ActivityStats.from(data.obj("stats")) to
            (0 until rows.length()).mapNotNull { rows.optJSONObject(it) }.map(Message::from)
    }

    suspend fun graph(tgId: Long): GraphData = GraphData.from(obj("api/targets/$tgId/graph"))

    suspend fun scan(tgId: Long, deep: Boolean = false): Job {
        val body = JSONObject().put("deep", deep)
        return Job.from(obj("api/targets/$tgId/scan", "POST", body))
    }

    suspend fun collect(tgId: Long): Job = Job.from(obj("api/targets/$tgId/activity/collect", "POST"))

    suspend fun jobs(): List<Job> = jsonObjects(array("api/jobs")).map(Job::from)

    suspend fun cancelJob(key: String): Boolean {
        val safe = URLEncoder.encode(key, "UTF-8")
        return obj("api/jobs/$safe/cancel", "POST").bool("ok")
    }

    suspend fun refreshProfile(tgId: Long): Target {
        val data = obj("api/targets/$tgId/profile/refresh", "POST", null, PROFILE_TIMEOUT)
        return Target.from(data.obj("target") ?: data)
    }

    suspend fun photo(tgId: Long): PhotoData? = try {
        val data = obj("api/targets/$tgId/photo")
        PhotoData(base64 = data.str("b64").orEmpty(), updatedAt = data.num("updated_at"))
    } catch (exc: ApiException) {
        if (exc.code == 404) null else throw exc
    }

    suspend fun gifts(
        owner: Long? = null,
        nftOnly: Boolean = false,
        chain: String? = null,
        q: String? = null,
        limit: Int = 100,
        offset: Int = 0,
    ): GiftPage {
        val path = buildString {
            append("api/gifts?limit=$limit&offset=$offset")
            if (owner != null) append("&owner=$owner")
            if (nftOnly) append("&nft=true")
            if (!chain.isNullOrBlank()) append("&chain=$chain")
            if (!q.isNullOrBlank()) append("&q=${URLEncoder.encode(q, "UTF-8")}")
        }
        val data = obj(path)
        val rows = jsonObjects(data.arr("rows")).map(GiftItem::from)
        val owners = jsonObjects(data.arr("owners")).map {
            GiftOwner(
                tgId = it.num("owner_tg_id"),
                gifts = it.int("gifts"),
                nft = it.int("nft"),
                lastDate = it.num("last_date"),
                username = it.str("owner_username"),
                name = it.str("owner_name"),
                onChain = it.int("on_chain"),
                hosted = it.int("hosted"),
            )
        }
        return GiftPage(rows = rows, summary = GiftSummary.from(data.obj("summary")), owners = owners)
    }

    /** NFT-инвентарь: `chain` = wallet (на кошельке) | telegram (в аккаунте). */
    suspend fun giftsNft(
        chain: String? = null,
        q: String? = null,
        sort: String = "value",
        limit: Int = 100,
        offset: Int = 0,
    ): NftPage {
        val path = buildString {
            append("api/gifts/nft?limit=$limit&offset=$offset&sort=$sort")
            if (!chain.isNullOrBlank()) append("&chain=$chain")
            if (!q.isNullOrBlank()) append("&q=${URLEncoder.encode(q, "UTF-8")}")
        }
        val data = obj(path)
        return NftPage(
            rows = jsonObjects(data.arr("rows")).map(NftItem::from),
            summary = NftSummary.from(data.obj("summary")),
            owners = jsonObjects(data.arr("owners")).map(NftOwner::from),
            collections = jsonObjects(data.arr("collections")).map(NftCollection::from),
        )
    }

    suspend fun nftOwners(
        chain: String? = null,
        q: String? = null,
        limit: Int = 100,
        offset: Int = 0,
    ): List<NftOwner> {
        val path = buildString {
            append("api/gifts/nft/owners?limit=$limit&offset=$offset")
            if (!chain.isNullOrBlank()) append("&chain=$chain")
            if (!q.isNullOrBlank()) append("&q=${URLEncoder.encode(q, "UTF-8")}")
        }
        return jsonObjects(obj(path).arr("rows")).map(NftOwner::from)
    }

    suspend fun startNftChain(requests: Int = 2000, discover: Boolean = true): Job {
        val body = JSONObject().put("requests", requests).put("discover", discover)
        return Job.from(obj("api/gifts/chain", "POST", body))
    }

    suspend fun refreshGifts(limit: Int = 60, perProfile: Int = 100): Job {
        val body = JSONObject().put("limit", limit).put("per_profile", perProfile)
        return Job.from(obj("api/gifts/refresh", "POST", body))
    }

    suspend fun crawlGifts(
        query: String,
        depth: Int = 1,
        maxProfiles: Int = 5,
        perProfile: Int = 100,
    ): Job {
        val body = JSONObject()
            .put("query", query)
            .put("depth", depth)
            .put("max_profiles", maxProfiles)
            .put("per_profile", perProfile)
        return Job.from(obj("api/gifts/crawl", "POST", body))
    }

    // ── ручное пополнение базы ──────────────────────────────────────────────

    suspend fun parseImport(text: String): ImportPreview {
        val data = obj("api/catalog/parse", "POST", JSONObject().put("text", text))
        val items = jsonObjects(data.arr("items")).map {
            ImportItem(
                raw = it.str("raw").orEmpty(),
                kind = it.str("kind").orEmpty(),
                value = it.str("value").orEmpty(),
                link = it.str("link"),
            )
        }
        val invalid = jsonObjects(data.arr("invalid")).map {
            ImportIssue(raw = it.str("raw").orEmpty(), reason = it.str("reason"))
        }
        val sessions = buildList {
            val arr = data.arr("sessions")
            for (index in 0 until arr.length()) add(arr.optString(index))
        }
        return ImportPreview(
            items = items,
            invalid = invalid,
            stats = importStatsFrom(data.obj("stats")),
            sessions = sessions.filter { it.isNotBlank() },
        )
    }

    suspend fun importGroups(text: String, join: Boolean, archive: Boolean): Job {
        val body = JSONObject()
            .put("text", text)
            .put("join", join)
            .put("archive", archive)
        return Job.from(obj("api/catalog/import", "POST", body))
    }

    suspend fun updateGroup(tgId: Long, title: String?, username: String?): Chat {
        val body = JSONObject()
        if (title != null) body.put("title", title)
        if (username != null) body.put("username", username)
        val data = obj("api/catalog/$tgId/edit", "POST", body)
        return Chat.from(data.obj("group") ?: data)
    }

    suspend fun deleteGroup(tgId: Long) {
        obj("api/catalog/$tgId", "DELETE", null)
    }

    suspend fun find(query: String): List<Chat> {
        val data = obj("api/find?q=${URLEncoder.encode(query, "UTF-8")}")
        val rows = data.arr("rows")
        return (0 until rows.length()).mapNotNull { rows.optJSONObject(it) }.map(Chat::from)
    }

    suspend fun catalogPage(q: String?, page: Int = 1, size: Int = 30): CatalogPage {
        val query = buildString {
            append("api/catalog?page=$page&size=$size")
            if (!q.isNullOrBlank()) append("&q=${URLEncoder.encode(q, "UTF-8")}")
        }
        return CatalogPage.from(obj(query))
    }

    suspend fun bootstrap(join: Boolean = false): Job =
        Job.from(obj("api/bootstrap?join=$join", "POST"))

    suspend fun catalog(q: String?, page: Int = 1, size: Int = 30): List<Chat> {
        val query = buildString {
            append("api/catalog?page=$page&size=$size")
            if (!q.isNullOrBlank()) append("&q=${URLEncoder.encode(q, "UTF-8")}")
        }
        val rows = obj(query).arr("rows")
        return (0 until rows.length()).mapNotNull { rows.optJSONObject(it) }.map(Chat::from)
    }

    // ── транспорт ────────────────────────────────────────────────────────────

    private suspend fun obj(
        path: String,
        method: String = "GET",
        body: JSONObject? = null,
        timeoutMillis: Int = READ_TIMEOUT,
    ): JSONObject = JSONObject(request(path, method, body, timeoutMillis))

    private suspend fun array(path: String): JSONArray = JSONArray(request(path))

    private suspend fun request(
        path: String,
        method: String = "GET",
        body: JSONObject? = null,
        timeoutMillis: Int = READ_TIMEOUT,
    ): String = withContext(Dispatchers.IO) {
        val url = URL(root + path)
        val conn = (url.openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 4_000
            readTimeout = timeoutMillis
            setRequestProperty("Accept", "application/json")
            if (token.isNotBlank()) setRequestProperty("X-Api-Token", token)
        }
        try {
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (code !in 200..299) {
                throw ApiException(code, humanError(code, text))
            }
            text
        } catch (exc: ApiException) {
            throw exc
        } catch (exc: Exception) {
            throw ApiException(0, exc.message ?: "движок недоступен")
        } finally {
            conn.disconnect()
        }
    }

    private companion object {
        const val READ_TIMEOUT = 12_000
        // Telegram может думать дольше: отправка кода и проверка 2FA — сетевые запросы.
        const val LOGIN_TIMEOUT = 40_000
        // Загрузка профиля: get_entity + full user + скачивание фото.
        const val PROFILE_TIMEOUT = 60_000
        // Живой добор сообщений: несколько чатов подряд, поэтому с запасом.
        const val LIVE_TIMEOUT = 35_000
    }

    private fun humanError(code: Int, raw: String): String {
        val trimmed = raw.trim()
        if (trimmed.startsWith("{")) {
            runCatching {
                val detail = JSONObject(trimmed).optString("detail", "")
                if (detail.isNotBlank()) return detail
            }
        }
        if (trimmed.isNotEmpty() && trimmed.length < 300) return trimmed
        return when (code) {
            401 -> "Неверный API_TOKEN"
            404 -> "Не найдено"
            409 -> "Действие уже выполняется"
            else -> "Ошибка движка (HTTP $code)"
        }
    }
}


internal fun jsonObjects(arr: JSONArray): List<JSONObject> {
    val out = ArrayList<JSONObject>(arr.length())
    for (i in 0 until arr.length()) {
        arr.optJSONObject(i)?.let(out::add)
    }
    return out
}
