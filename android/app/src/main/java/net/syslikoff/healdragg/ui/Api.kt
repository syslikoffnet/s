package net.syslikoff.healdragg.ui

import android.content.Context
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import android.util.Base64
import java.net.URLEncoder

data class TargetPage(val rows: List<Target>, val nextBefore: Long?)
data class ObservedPerson(val tgId: Long, val name: String?, val username: String?, val source: String, val observedAt: Long)
data class ObservedPeoplePage(val rows: List<ObservedPerson>, val nextBefore: Long?)
data class ProfileGiftPage(val rows: List<GiftItem>, val nextBefore: Long?)
data class GiftSearchResult(val gifts: List<GiftItem>, val nft: List<NftItem>)

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

    suspend fun targetsPage(query: String, before: Long? = null): TargetPage {
        val encoded = URLEncoder.encode(query, "UTF-8")
        val data = obj("api/targets/page?limit=50&q=$encoded" + (before?.let { "&before=$it" } ?: ""))
        val next = if (data.isNull("next_before")) null else data.getLong("next_before")
        return TargetPage(jsonObjects(data.arr("rows")).map(Target::from), next)
    }

    suspend fun searchPeople(query: String, before: Long? = null): ObservedPeoplePage {
        val data = obj("api/search/people?limit=25&q=${URLEncoder.encode(query, "UTF-8")}" + (before?.let { "&before=$it" } ?: ""))
        val rows = jsonObjects(data.arr("rows")).map {
            ObservedPerson(it.num("tg_id"), it.str("name"), it.str("username"), it.str("source").orEmpty(), it.num("observed_at"))
        }
        val next = if (data.isNull("next_before")) null else data.getLong("next_before")
        return ObservedPeoplePage(rows, next)
    }

    suspend fun findTarget(query: String): Target =
        Target.from(obj("api/targets/lookup?q=${URLEncoder.encode(query, "UTF-8")}"))

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

    suspend fun businessBot(name: String, connect: Boolean = false): JSONObject {
        val path = "api/sessions/${URLEncoder.encode(name, "UTF-8")}/business-bot"
        return if (connect) obj(path, "POST", JSONObject().put("confirm", true), PROFILE_TIMEOUT)
        else obj(path, timeoutMillis = PROFILE_TIMEOUT)
    }

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
        return Target.from(obj("api/profiles/open", "POST", body))
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

    suspend fun lookupNft(slug: String): NftItem {
        val data = obj("api/search/nft", "POST", JSONObject().put("slug", slug), PROFILE_TIMEOUT)
        return NftItem.from(data.getJSONObject("item"))
    }

    suspend fun collectProfileGifts(tgId: Long): Job =
        Job.from(obj("api/targets/$tgId/gifts/collect", "POST", JSONObject()))

    suspend fun profileGifts(tgId: Long, direction: String, before: Long? = null): ProfileGiftPage {
        val data = obj("api/targets/$tgId/gifts?direction=$direction&limit=30" + (before?.let { "&before=$it" } ?: ""))
        return ProfileGiftPage(jsonObjects(data.arr("rows")).map(GiftItem::from),
            if (data.isNull("next_before")) null else data.getLong("next_before"))
    }

    suspend fun searchGifts(query: String): GiftSearchResult {
        val data = obj("api/search/gifts?q=${URLEncoder.encode(query, "UTF-8")}")
        return GiftSearchResult(jsonObjects(data.arr("gifts")).map(GiftItem::from),
            jsonObjects(data.arr("nft")).map(NftItem::from))
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
        return GiftPage(rows = rows, summary = GiftSummary.from(data.obj("summary")), owners = owners, total = data.int("total"))
    }

    suspend fun giftOwners(q: String?, limit: Int = 500): List<NftOwner> {
        val query = q?.let { "&q=${URLEncoder.encode(it, "UTF-8")}" } ?: ""
        return jsonObjects(obj("api/gifts/owners?limit=$limit$query").arr("rows")).map(NftOwner::from)
    }

    suspend fun heavyGiftStatus(id: Long): JSONObject? = obj("api/gifts/owners/$id/heavy").obj("task")

    suspend fun failedHeavyTasks(): List<JSONObject> = jsonObjects(obj("api/gifts/heavy/tasks").arr("rows"))

    suspend fun dismissHeavyError(id: Long) { obj("api/gifts/owners/$id/heavy/dismiss", "POST") }

    suspend fun startHeavyGifts(id: Long, session: String, watch: Boolean): Job {
        val body = JSONObject().put("session", session).put("watch", watch)
        return Job.from(obj("api/gifts/owners/$id/heavy", "POST", body))
    }

    suspend fun giftOwnerNft(id: Long): List<NftItem> =
        jsonObjects(obj("api/gifts/owners/$id/nft").arr("rows")).map(NftItem::from)

    suspend fun giftOwnerProfile(id: Long, username: String?): JSONObject {
        val query = username?.let { "?username=${URLEncoder.encode(it, "UTF-8")}" } ?: ""
        return obj("api/gifts/owners/$id/profile$query", timeoutMillis = PROFILE_TIMEOUT)
    }

    suspend fun giftHistory(slug: String): List<JSONObject> =
        jsonObjects(obj("api/gifts/nft/${URLEncoder.encode(slug, "UTF-8")}/history").arr("rows"))

    /** NFT-инвентарь: `chain` = wallet (на кошельке) | telegram (в аккаунте). */
    suspend fun giftsNft(
        chain: String? = null,
        q: String? = null,
        sort: String = "value",
        limit: Int = 100,
        offset: Int = 0,
        resaleOnly: Boolean = false,
        collection: String? = null,
    ): NftPage {
        val path = buildString {
            append("api/gifts/nft?limit=$limit&offset=$offset&sort=$sort")
            if (!chain.isNullOrBlank()) append("&chain=$chain")
            if (!q.isNullOrBlank()) append("&q=${URLEncoder.encode(q, "UTF-8")}")
            if (resaleOnly) append("&resell=1")
            if (!collection.isNullOrBlank()) append("&collection=${URLEncoder.encode(collection, "UTF-8")}")
        }
        val data = obj(path)
        return NftPage(
            rows = jsonObjects(data.arr("rows")).map(NftItem::from),
            summary = NftSummary.from(data.obj("summary")),
            owners = jsonObjects(data.arr("owners")).map(NftOwner::from),
            collections = jsonObjects(data.arr("collections")).map(NftCollection::from),
            total = data.num("total").toInt(),
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

    /** Выгрузка NFT-инвентаря в CSV (для Excel/Sheets). */
    suspend fun nftCsv(
        chain: String? = null,
        q: String? = null,
        sort: String = "value",
        resaleOnly: Boolean = false,
        collection: String? = null,
    ): ByteArray {
        val path = buildString {
            append("api/gifts/nft/export.csv?sort=$sort&limit=100000")
            if (!chain.isNullOrBlank()) append("&chain=$chain")
            if (!q.isNullOrBlank()) append("&q=${URLEncoder.encode(q, "UTF-8")}")
            if (resaleOnly) append("&resell=1")
            if (!collection.isNullOrBlank()) append("&collection=${URLEncoder.encode(collection, "UTF-8")}")
        }
        return raw(path, "GET", null, "text/csv")
    }

    /** Общая OSINT-карта: цели ↔ чаты ↔ собеседники. */
    suspend fun globalGraph(targets: Int = 60, chats: Int = 120, peers: Int = 200): GlobalMap {
        val data = obj("api/graph/global?targets=$targets&chats=$chats&peers=$peers")
        val stats = data.obj("stats")
        return GlobalMap(
            nodes = jsonObjects(data.arr("nodes")).map(MapNode::from),
            links = jsonObjects(data.arr("links")).map { row ->
                MapLink(
                    source = row.str("source").orEmpty(),
                    target = row.str("target").orEmpty(),
                    kind = row.str("kind").orEmpty(),
                    weight = row.num("weight").toInt(),
                )
            },
            targets = stats?.num("targets")?.toInt() ?: 0,
            chats = stats?.num("chats")?.toInt() ?: 0,
            peers = stats?.num("peers")?.toInt() ?: 0,
        )
    }

    /** Валюта приложения и курсы — цены на карточках считаются в ней. */
    suspend fun displaySettings(): JSONObject = obj("api/settings/display")

    suspend fun saveDisplay(currency: String, usdRub: Double, tonUsd: Double, starUsd: Double): JSONObject {
        val body = JSONObject()
            .put("currency", currency)
            .put(
                "rates",
                JSONObject()
                    .put("usd_rub", usdRub)
                    .put("ton_usd", tonUsd)
                    .put("star_usd", starUsd),
            )
        return obj("api/settings/display", "POST", body)
    }

    // ── аккаунты и резервные копии ──────────────────────────────────────────

    suspend fun importSession(filename: String, payload: ByteArray): SessionImportResult {
        val body = JSONObject()
            .put("filename", filename)
            .put("content_b64", Base64.encodeToString(payload, Base64.NO_WRAP))
        val data = obj("api/sessions/import", "POST", body, IMPORT_TIMEOUT)
        val imported = jsonObjects(data.arr("imported")).map {
            ImportedSession(
                name = it.str("name").orEmpty(),
                tgUserId = it.num("tg_user_id"),
                dcId = it.int("dc_id"),
                source = it.str("source"),
            )
        }
        val errors = jsonObjects(data.arr("errors")).map {
            ImportError(file = it.str("file").orEmpty(), error = it.str("error").orEmpty())
        }
        return SessionImportResult(imported = imported, errors = errors)
    }

    suspend fun backups(): BackupPage {
        val data = obj("api/backup")
        val rows = jsonObjects(data.arr("backups")).map { BackupInfo.from(it) }
        return BackupPage(
            rows = rows,
            latest = data.obj("latest")?.let { BackupInfo.from(it) },
            counts = data.obj("counts"),
        )
    }

    suspend fun createBackup(): BackupInfo =
        BackupInfo.from(obj("api/backup/create", "POST", null, BACKUP_TIMEOUT))

    /** Свежая копия (или конкретная по имени — для списка копий в настройках). */
    suspend fun downloadBackup(name: String? = null): ByteArray {
        val path = if (name.isNullOrBlank()) {
            "api/backup/download"
        } else {
            "api/backup/download?name=${URLEncoder.encode(name, "UTF-8")}"
        }
        return raw(path, "GET", null, "application/zip")
    }

    suspend fun restoreBackup(context: Context, uri: Uri): JSONObject = withContext(Dispatchers.IO) {
        val conn = (URL(root + "api/backup/restore").openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"
            connectTimeout = 5_000
            readTimeout = BACKUP_TIMEOUT
            doOutput = true
            setChunkedStreamingMode(64 * 1024)
            setRequestProperty("Content-Type", "application/zip")
            if (token.isNotBlank()) setRequestProperty("X-Api-Token", token)
        }
        try {
            val input = context.contentResolver.openInputStream(uri)
                ?: throw ApiException(0, "Не удалось открыть файл копии")
            input.use { source ->
                conn.outputStream.use { target ->
                    val buffer = ByteArray(64 * 1024)
                    var total = 0L
                    while (true) {
                        val count = source.read(buffer)
                        if (count < 0) break
                        total += count
                        if (total > 400L * 1024 * 1024) throw ApiException(413, "Копия больше 400 МБ")
                        target.write(buffer, 0, count)
                    }
                }
            }
            val code = conn.responseCode
            val text = (if (code in 200..299) conn.inputStream else conn.errorStream)
                ?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (code !in 200..299) throw ApiException(code, humanError(code, text))
            JSONObject(text).obj("counts") ?: JSONObject()
        } catch (exc: ApiException) {
            throw exc
        } catch (exc: Exception) {
            throw ApiException(0, exc.message ?: "Не удалось восстановить копию")
        } finally {
            conn.disconnect()
        }
    }

    suspend fun giftArt(giftId: Long): ByteArray =
        raw("api/gifts/art/$giftId", timeoutMillis = 45_000)

    suspend fun giftRuns(limit: Int = 100, archived: Boolean = false): List<JSONObject> =
        jsonObjects(obj("api/gifts/runs?attention=true&limit=$limit&archived=$archived").arr("rows"))

    suspend fun archiveGiftRun(id: Long, archived: Boolean) {
        obj("api/gifts/runs/$id/archive", "POST", JSONObject().put("archived", archived))
    }

    suspend fun giftRunIssues(id: Long, offset: Int): Pair<List<JSONObject>, Int> {
        val data = obj("api/gifts/runs/$id/issues?limit=25&offset=$offset")
        return jsonObjects(data.arr("rows")) to data.int("total")
    }

    suspend fun resumeGiftRun(id: Long): Job = Job.from(obj("api/gifts/runs/$id/resume", "POST"))

    suspend fun startNftProfiles(limit: Int = 200, depth: Int = 2): Job {
        val body = JSONObject().put("limit", limit).put("depth", depth).put("per_profile", 1000)
        return Job.from(obj("api/gifts/nft/profiles", "POST", body))
    }

    suspend fun nftChainStatus(): JSONObject? = obj("api/gifts/chain").obj("task")

    suspend fun nftChainRuns(archived: Boolean = false, limit: Int = 100): List<JSONObject> =
        jsonObjects(obj("api/gifts/chain/runs?limit=$limit&archived=$archived").arr("rows"))

    suspend fun archiveNftChain(id: Long, archived: Boolean) {
        obj("api/gifts/chain/runs/$id/archive", "POST", JSONObject().put("archived", archived))
    }

    suspend fun resumeNftChain(id: Long, extraRequests: Int = 0): Job = Job.from(
        obj("api/gifts/chain/runs/$id/resume", "POST", JSONObject().put("extra_requests", extraRequests))
    )

    suspend fun startNftChain(requests: Int = 2000, discover: Boolean = true): Job {
        val body = JSONObject().put("requests", requests).put("discover", discover)
        return Job.from(obj("api/gifts/chain", "POST", body))
    }

    suspend fun refreshGifts(limit: Int = 60, perProfile: Int = 1000): Job {
        val body = JSONObject().put("limit", limit).put("per_profile", perProfile)
        return Job.from(obj("api/gifts/refresh", "POST", body))
    }

    suspend fun crawlGifts(
        query: String,
        depth: Int = 1,
        maxProfiles: Int = 5,
        perProfile: Int = 1000,
        refreshKnown: Boolean = false,
    ): Job {
        val body = JSONObject()
            .put("query", query)
            .put("depth", depth)
            .put("max_profiles", maxProfiles)
            .put("per_profile", perProfile)
            .put("refresh_known", refreshKnown)
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

    private suspend fun raw(
        path: String,
        method: String = "GET",
        body: ByteArray? = null,
        contentType: String = "application/octet-stream",
        timeoutMillis: Int = BACKUP_TIMEOUT,
    ): ByteArray = withContext(Dispatchers.IO) {
        val url = URL(root + path)
        val conn = (url.openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 5_000
            readTimeout = timeoutMillis
            if (token.isNotBlank()) setRequestProperty("X-Api-Token", token)
        }
        try {
            if (body != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", contentType)
                conn.outputStream.use { it.write(body) }
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else conn.errorStream
            val bytes = stream?.use { it.readBytes() } ?: ByteArray(0)
            if (code !in 200..299) {
                throw ApiException(code, humanError(code, String(bytes, Charsets.UTF_8)))
            }
            bytes
        } catch (exc: ApiException) {
            throw exc
        } catch (exc: Exception) {
            throw ApiException(0, exc.message ?: "движок недоступен")
        } finally {
            conn.disconnect()
        }
    }

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
        // Импорт сессии/tdata: файл передаётся base64 в теле запроса.
        const val IMPORT_TIMEOUT = 180_000
        // Бэкап может быть большим: база + сессии.
        const val BACKUP_TIMEOUT = 180_000
    }

    private fun humanError(code: Int, raw: String): String {
        val trimmed = raw.trim()
        if (trimmed.startsWith("{")) {
            runCatching {
                val json = JSONObject(trimmed)
                for (key in listOf("error", "detail", "message")) {
                    val value = json.optString(key, "")
                    if (value.isNotBlank()) return value
                }
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
