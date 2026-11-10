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

    suspend fun targets(): List<Target> =
        array("api/targets").mapNotNull { it as? JSONObject }.map(Target::from)

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

    suspend fun graph(tgId: Long): GraphData = GraphData.from(obj("api/targets/$tgId/graph"))

    suspend fun scan(tgId: Long, deep: Boolean = false): Job {
        val body = JSONObject().put("deep", deep)
        return Job.from(obj("api/targets/$tgId/scan", "POST", body))
    }

    suspend fun collect(tgId: Long): Job = Job.from(obj("api/targets/$tgId/activity/collect", "POST"))

    suspend fun jobs(): List<Job> =
        array("api/jobs").mapNotNull { it as? JSONObject }.map(Job::from)

    suspend fun cancelJob(key: String): Boolean {
        val safe = URLEncoder.encode(key, "UTF-8")
        return obj("api/jobs/$safe/cancel", "POST").bool("ok")
    }

    suspend fun find(query: String): List<Chat> {
        val data = obj("api/find?q=${URLEncoder.encode(query, "UTF-8")}")
        val rows = data.arr("rows")
        return (0 until rows.length()).mapNotNull { rows.optJSONObject(it) }.map(Chat::from)
    }

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
    ): JSONObject = JSONObject(request(path, method, body))

    private suspend fun array(path: String): JSONArray = JSONArray(request(path))

    private suspend fun request(
        path: String,
        method: String = "GET",
        body: JSONObject? = null,
    ): String = withContext(Dispatchers.IO) {
        val url = URL(root + path)
        val conn = (url.openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 4_000
            readTimeout = 12_000
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
