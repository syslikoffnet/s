package net.syslikoff.healdragg.ui

import android.app.Application
import android.content.Context
import android.os.PowerManager
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import net.syslikoff.healdragg.EngineService

enum class EngineState { Unknown, Starting, Ready, Stopped, Error }

/**
 * Общее состояние приложения: настройки, движок, цели, алерты.
 * Живёт в ViewModel, поэтому переживает поворот экрана и смену темы.
 */
class AppViewModel(app: Application) : AndroidViewModel(app) {

    private val prefs = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    var baseUrl by mutableStateOf(prefs.getString(KEY_URL, LOCAL_URL).orEmpty())
        private set
    var token by mutableStateOf(prefs.getString(KEY_TOKEN, "").orEmpty())
        private set
    var apiId by mutableStateOf(prefs.getString(KEY_API_ID, "").orEmpty())
        private set
    var apiHash by mutableStateOf(prefs.getString(KEY_API_HASH, "").orEmpty())
        private set

    var engine by mutableStateOf(EngineState.Unknown)
        private set
    var engineError by mutableStateOf<String?>(null)
        private set
    var engineBusy by mutableStateOf(false)
        private set

    var targets by mutableStateOf<List<Target>>(emptyList())
        private set
    var stats by mutableStateOf<Stats?>(null)
        private set
    var alerts by mutableStateOf<List<AlertItem>>(emptyList())
        private set
    var unread by mutableStateOf(0)
        private set

    var loading by mutableStateOf(false)
        private set
    var refreshing by mutableStateOf(false)
        private set
    var offline by mutableStateOf(false)
        private set
    var lastError by mutableStateOf<String?>(null)
        private set

    var api: Api? = null
        private set

    val hasCredentials: Boolean
        get() = apiId.isNotBlank() && apiHash.length >= 16

    val isLocal: Boolean
        get() = baseUrl == LOCAL_URL || baseUrl.startsWith(LOCAL_HOST)

    private var pollJob: Job? = null
    private var firstLoad = true

    init {
        rebuildApi()
        startPolling()
    }

    private fun rebuildApi() {
        api = if (token.isBlank()) null else Api(baseUrl, token)
    }

    // ── настройки ────────────────────────────────────────────────────────────

    fun saveCredentials(id: String, hash: String) {
        apiId = id.trim()
        apiHash = hash.trim()
        prefs.edit()
            .putString(KEY_API_ID, apiId)
            .putString(KEY_API_HASH, apiHash)
            .apply()
    }

    fun saveServer(url: String, tokenValue: String) {
        baseUrl = normalize(url)
        token = tokenValue.trim()
        prefs.edit()
            .putString(KEY_URL, baseUrl)
            .putString(KEY_TOKEN, token)
            .apply()
        rebuildApi()
        engine = EngineState.Unknown
        startPolling()
        refresh(force = true)
    }

    fun useLocalEngine() {
        saveServer(LOCAL_URL, token)
    }

    fun ensureToken(): String {
        if (token.length >= 16) return token
        val bytes = ByteArray(18).also { java.security.SecureRandom().nextBytes(it) }
        token = bytes.joinToString("") { "%02x".format(it) }
        prefs.edit().putString(KEY_TOKEN, token).apply()
        rebuildApi()
        return token
    }

    // ── движок ───────────────────────────────────────────────────────────────

    fun startEngine() {
        if (!hasCredentials) {
            lastError = "Сначала впиши API_ID и API_HASH"
            return
        }
        engineBusy = true
        engine = EngineState.Starting
        engineError = null
        ensureToken()
        prefs.edit()
            .putString(KEY_URL, LOCAL_URL)
            .apply()
        baseUrl = LOCAL_URL
        rebuildApi()
        EngineService.start(getApplication())
        viewModelScope.launch {
            val deadline = System.currentTimeMillis() + 90_000
            while (System.currentTimeMillis() < deadline) {
                delay(1_000)
                if (api?.health() == true) {
                    engine = EngineState.Ready
                    engineBusy = false
                    refresh(force = true)
                    return@launch
                }
                val state = prefs.getString(KEY_ENGINE_STATE, "").orEmpty()
                if (state == EngineService.STATE_ERROR) {
                    engineError = prefs.getString(KEY_ENGINE_ERROR, "").orEmpty()
                    engine = EngineState.Error
                    engineBusy = false
                    return@launch
                }
            }
            engine = EngineState.Error
            engineError = "Движок не ответил за 90 секунд"
            engineBusy = false
        }
    }

    fun stopEngine() {
        EngineService.stop(getApplication())
        engine = EngineState.Stopped
        engineBusy = false
        engineError = null
    }

    fun restartEngine() {
        stopEngine()
        viewModelScope.launch {
            delay(600)
            startEngine()
        }
    }

    fun engineLog(): String = try {
        val file = java.io.File(getApplication<Application>().filesDir, "engine/logs/engine.log")
        file.readLines().takeLast(400).joinToString("\n").ifBlank { "Лог пока пуст." }
    } catch (_: Exception) {
        "Лог недоступен."
    }

    fun batteryOptimized(): Boolean {
        val app = getApplication<Application>()
        val manager = app.getSystemService(PowerManager::class.java) ?: return false
        return manager.isIgnoringBatteryOptimizations(app.packageName)
    }

    private fun startPolling() {
        pollJob?.cancel()
        pollJob = viewModelScope.launch {
            while (isActive) {
                probe()
                delay(if (engine == EngineState.Ready) 15_000 else 3_000)
            }
        }
    }

    private suspend fun probe() {
        val alive = api?.health() == true
        offline = !alive
        if (alive) {
            if (engine != EngineState.Ready) {
                engine = EngineState.Ready
                engineBusy = false
                if (firstLoad) refresh(force = true)
            }
        } else {
            val state = prefs.getString(KEY_ENGINE_STATE, "").orEmpty()
            engine = when {
                engineBusy || state == EngineService.STATE_STARTING -> EngineState.Starting
                state == EngineService.STATE_ERROR -> EngineState.Error
                state == EngineService.STATE_STOPPED -> EngineState.Stopped
                isLocal -> EngineState.Stopped
                else -> EngineState.Unknown
            }
            if (engine == EngineState.Error) {
                engineError = prefs.getString(KEY_ENGINE_ERROR, "").orEmpty().ifBlank { null }
            }
        }
    }

    // ── данные ───────────────────────────────────────────────────────────────

    fun refresh(force: Boolean = false) {
        val client = api ?: return
        if (refreshing) return
        refreshing = true
        if (firstLoad) loading = true
        viewModelScope.launch {
            try {
                val freshTargets = client.targets()
                val freshStats = runCatching { client.stats() }.getOrNull()
                val (freshAlerts, freshUnread) = client.alerts()
                targets = freshTargets
                stats = freshStats
                alerts = freshAlerts
                unread = freshUnread
                lastError = null
                offline = false
            } catch (exc: Exception) {
                lastError = exc.message
                offline = true
            } finally {
                refreshing = false
                loading = false
                firstLoad = false
            }
        }
    }

    fun refreshAlerts() {
        val client = api ?: return
        viewModelScope.launch {
            runCatching { client.alerts() }.onSuccess { (rows, count) ->
                alerts = rows
                unread = count
            }
        }
    }

    fun markAllAlertsRead() {
        val client = api ?: return
        viewModelScope.launch {
            runCatching { client.markAlertsRead() }.onSuccess { left ->
                unread = left
                alerts = alerts.map { it.copy(read = true) }
                prefs.edit().putInt(KEY_UNREAD, left).apply()
            }
        }
    }

    fun markAlertRead(id: Long) {
        val client = api ?: return
        viewModelScope.launch {
            runCatching { client.markAlertsRead(listOf(id)) }.onSuccess { left ->
                unread = left
                alerts = alerts.map { if (it.id == id) it.copy(read = true) else it }
            }
        }
    }

    fun addTarget(query: String, onDone: (Result<Target>) -> Unit) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch {
            val result = runCatching { client.addTarget(query) }
            result.onSuccess {
                refresh()
                onDone(result)
            }.onFailure { onDone(Result.failure(it)) }
        }
    }

    fun deleteTarget(tgId: Long, onDone: (Boolean) -> Unit = {}) {
        val client = api ?: return
        viewModelScope.launch {
            val ok = runCatching { client.deleteTarget(tgId) }.isSuccess
            if (ok) {
                targets = targets.filterNot { it.tgId == tgId }
                refresh()
            }
            onDone(ok)
        }
    }

    fun startScan(tgId: Long, deep: Boolean, onDone: (Result<Job>) -> Unit) {
        val client = api ?: return
        viewModelScope.launch {
            onDone(runCatching { client.scan(tgId, deep) })
        }
    }

    fun startCollect(tgId: Long, onDone: (Result<Job>) -> Unit) {
        val client = api ?: return
        viewModelScope.launch {
            onDone(runCatching { client.collect(tgId) })
        }
    }

    fun targetByTgId(tgId: Long): Target? = targets.firstOrNull { it.tgId == tgId }

    private fun normalize(raw: String): String {
        var value = raw.trim()
        if (value.isEmpty()) return LOCAL_URL
        if (!value.startsWith("http://") && !value.startsWith("https://")) value = "http://$value"
        if (!value.endsWith("/")) value += "/"
        return value
    }

    fun saveUnread(count: Int) {
        prefs.edit().putInt(KEY_UNREAD, count).apply()
    }

    companion object {
        const val PREFS = "healdragg"
        const val KEY_URL = "base_url"
        const val KEY_TOKEN = "api_token"
        const val KEY_API_ID = "tg_api_id"
        const val KEY_API_HASH = "tg_api_hash"
        const val KEY_UNREAD = "notified_unread"
        const val KEY_ENGINE_STATE = "engine_state"
        const val KEY_ENGINE_ERROR = "engine_error"
        const val LOCAL_URL = "http://127.0.0.1:8080/"
        const val LOCAL_HOST = "http://127.0.0.1"
    }
}
