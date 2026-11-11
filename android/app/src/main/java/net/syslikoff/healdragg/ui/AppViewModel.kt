package net.syslikoff.healdragg.ui

import android.app.Application
import android.content.Context
import android.os.PowerManager
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Job as CoroutineJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import net.syslikoff.healdragg.EngineService
import org.json.JSONObject

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

    /** Оформление: Material 3 (по умолчанию) или компактная моно-тема. */
    var md3 by mutableStateOf(prefs.getBoolean(KEY_MD3, true))
        private set

    var engine by mutableStateOf(EngineState.Unknown)
        private set
    var engineError by mutableStateOf<String?>(null)
        private set
    var engineBusy by mutableStateOf(false)
        private set

    var targets by mutableStateOf<List<Target>>(emptyList())
        private set
    var targetQuery by mutableStateOf("")
        private set
    var targetPageLoading by mutableStateOf(false)
        private set
    var targetPageError by mutableStateOf<String?>(null)
        private set
    var targetNextBefore by mutableStateOf<Long?>(null)
        private set
    private var targetGeneration = 0L

    var stats by mutableStateOf<Stats?>(null)
        private set
    var alerts by mutableStateOf<List<AlertItem>>(emptyList())
        private set
    var unread by mutableStateOf(0)
        private set

    var sessions by mutableStateOf<List<SessionInfo>>(emptyList())
        private set

    /** Успели хотя бы раз прочитать список аккаунтов с движка. */
    var sessionsLoaded by mutableStateOf(false)
        private set

    /** Сбор базы: текущий (или последний) job и его ошибка. */
    var bootJob by mutableStateOf<Job?>(null)
        private set
    var bootError by mutableStateOf<String?>(null)
        private set
    var bootStarting by mutableStateOf(false)
        private set

    var profileBusy by mutableStateOf(false)
        private set

    val bootRunning: Boolean
        get() = bootJob?.let { !it.finished } ?: false

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

    /** Валюта карточек и курсы: приходят с сервера, можно менять в настройках. */
    var currency by mutableStateOf(prefs.getString(KEY_CURRENCY, "RUB").orEmpty())
        private set
    var displayLoaded = false
        private set

    val hasCredentials: Boolean
        get() = apiId.isNotBlank() && apiHash.length >= 16

    val hasSessions: Boolean
        get() = sessions.isNotEmpty()

    val isLocal: Boolean
        get() = baseUrl == LOCAL_URL || baseUrl.startsWith(LOCAL_HOST)

    private var pollJob: CoroutineJob? = null
    private var engineStartJob: CoroutineJob? = null
    private var firstLoad = true

    init {
        rebuildApi()
        startPolling()
    }

    private fun rebuildApi() {
        targetGeneration++
        targets = emptyList()
        targetNextBefore = null
        targetPageLoading = false
        targetPageError = null
        api = if (token.isBlank()) null else Api(baseUrl, token)
    }

    // ── настройки ────────────────────────────────────────────────────────────

    fun setMaterial3(enabled: Boolean) {
        if (md3 == enabled) return
        md3 = enabled
        prefs.edit().putBoolean(KEY_MD3, enabled).apply()
    }

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
        if (engineBusy) return
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
        engineStartJob?.cancel()
        engineStartJob = viewModelScope.launch {
            val deadline = System.currentTimeMillis() + 90_000
            while (System.currentTimeMillis() < deadline) {
                delay(1_000)
                val alive = api?.health() == true
                if (!isActive) return@launch
                val state = prefs.getString(KEY_ENGINE_STATE, "").orEmpty()
                if (alive && state == EngineService.STATE_READY) {
                    engine = EngineState.Ready
                    engineBusy = false
                    refresh(force = true)
                    return@launch
                }
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
        engineStartJob?.cancel()
        engineStartJob = null
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

    suspend fun loadDisplay() {
        val client = api ?: return
        val payload = runCatching { client.displaySettings() }.getOrNull() ?: return
        Money.apply(payload)
        displayLoaded = true
        currency = Money.currency
        prefs.edit().putString(KEY_CURRENCY, Money.currency).apply()
    }

    fun saveDisplay(
        chosen: String,
        usdRub: Double,
        tonUsd: Double,
        starUsd: Double,
        onDone: (Result<String>) -> Unit,
    ) {
        val client = api
        if (client == null) {
            onDone(Result.failure(IllegalStateException("Движок ещё не готов")))
            return
        }
        viewModelScope.launch {
            val result = runCatching { client.saveDisplay(chosen, usdRub, tonUsd, starUsd) }
            result.onSuccess { payload ->
                Money.apply(payload)
                currency = Money.currency
                prefs.edit().putString(KEY_CURRENCY, Money.currency).apply()
                onDone(Result.success(Money.currency))
            }.onFailure { onDone(Result.failure(it)) }
        }
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
        val state = prefs.getString(KEY_ENGINE_STATE, "").orEmpty()
        val available = alive && (!isLocal || state == EngineService.STATE_READY)
        offline = !available
        if (available) {
            if (engine != EngineState.Ready) {
                engine = EngineState.Ready
                engineBusy = false
                // всегда перечитываем состояние: иначе после старта движка
                // список аккаунтов оставался пустым и «аккаунт добавлен» пропадал
                refresh(force = true)
            }
            if (!displayLoaded) loadDisplay()
            refreshAlerts()
        } else {
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
        reloadTargets()
        if (refreshing) return
        refreshing = true
        if (firstLoad) loading = true
        viewModelScope.launch {
            try {
                val freshStats = runCatching { client.stats() }.getOrNull()
                val (freshAlerts, freshUnread) = client.alerts()
                val freshSessions = runCatching { client.sessions() }.getOrDefault(sessions)
                stats = freshStats
                alerts = freshAlerts
                unread = freshUnread
                sessions = freshSessions
                sessionsLoaded = true
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

    fun changeTargetQuery(query: String) {
        if (targetQuery == query) return
        targetQuery = query
        targets = emptyList()
        targetNextBefore = null
        targetPageError = null
        reloadTargets()
    }

    fun reloadTargets() {
        val client = api ?: return
        val generation = ++targetGeneration
        val query = targetQuery
        targetPageLoading = true
        targetPageError = null
        targetNextBefore = null
        viewModelScope.launch {
            try {
                val page = client.targetsPage(query)
                if (generation == targetGeneration && client === api) {
                    targets = page.rows
                    targetNextBefore = page.nextBefore
                }
            } catch (exc: Exception) {
                if (generation == targetGeneration && client === api) targetPageError = exc.message ?: "Не удалось загрузить цели"
            } finally {
                if (generation == targetGeneration && client === api) targetPageLoading = false
            }
        }
    }

    fun loadMoreTargets() {
        val client = api ?: return
        val cursor = targetNextBefore ?: return
        if (targetPageLoading) return
        val generation = targetGeneration
        val query = targetQuery
        targetPageLoading = true
        targetPageError = null
        viewModelScope.launch {
            try {
                val page = client.targetsPage(query, cursor)
                if (generation == targetGeneration && client === api) {
                    val seen = targets.mapTo(mutableSetOf()) { it.tgId }
                    targets = targets + page.rows.filter { seen.add(it.tgId) }
                    targetNextBefore = page.nextBefore
                }
            } catch (exc: Exception) {
                if (generation == targetGeneration && client === api) targetPageError = exc.message ?: "Не удалось загрузить цели"
            } finally {
                if (generation == targetGeneration && client === api) targetPageLoading = false
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
        val client = api ?: run {
            onDone(false)
            return
        }
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
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch {
            onDone(runCatching { client.scan(tgId, deep) })
        }
    }

    fun startCollect(tgId: Long, onDone: (Result<Job>) -> Unit) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch {
            onDone(runCatching { client.collect(tgId) })
        }
    }

    fun refreshSessions() {
        val client = api ?: return
        viewModelScope.launch {
            runCatching { client.sessions() }.onSuccess {
                sessions = it
                sessionsLoaded = true
            }
        }
    }

    // ── ручное пополнение базы ───────────────────────────────────────────────

    fun parseImport(text: String, onDone: (Result<ImportPreview>) -> Unit) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch { onDone(runCatching { client.parseImport(text) }) }
    }

    fun importGroups(
        text: String,
        join: Boolean,
        archive: Boolean,
        onDone: (Result<Job>) -> Unit,
    ) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch { onDone(runCatching { client.importGroups(text, join, archive) }) }
    }

    fun updateGroup(
        tgId: Long,
        title: String?,
        username: String?,
        onDone: (Result<Chat>) -> Unit,
    ) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch { onDone(runCatching { client.updateGroup(tgId, title, username) }) }
    }

    fun deleteGroup(tgId: Long, onDone: (Result<Unit>) -> Unit) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch {
            onDone(
                runCatching {
                    client.deleteGroup(tgId)
                },
            )
        }
    }

    // ── подарки ──────────────────────────────────────────────────────────────

    fun refreshGifts(
        limit: Int = 60,
        perProfile: Int = 1000,
        onDone: (Result<Job>) -> Unit,
    ) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch {
            onDone(runCatching { client.refreshGifts(limit = limit, perProfile = perProfile) })
        }
    }

    // ── аккаунты из файла и резервные копии ─────────────────────────────────

    fun importSession(filename: String, payload: ByteArray, onDone: (Result<SessionImportResult>) -> Unit) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch {
            val result = runCatching { client.importSession(filename, payload) }
            result.onSuccess { refresh() }
            onDone(result)
        }
    }

    fun createBackup(onDone: (Result<BackupInfo>) -> Unit) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch { onDone(runCatching { client.createBackup() }) }
    }

    fun downloadBackup(onDone: (Result<ByteArray>) -> Unit) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch { onDone(runCatching { client.downloadBackup() }) }
    }

    fun restoreBackup(uri: android.net.Uri, onDone: (Result<JSONObject>) -> Unit) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch {
            val result = runCatching { client.restoreBackup(getApplication(), uri) }
            result.onSuccess { refresh() }
            onDone(result)
        }
    }

    /** Читает подарки владельцев NFT: сеть растёт по дарителям. */
    fun startNftProfiles(limit: Int, onDone: (Result<Job>) -> Unit) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch {
            onDone(runCatching { client.startNftProfiles(limit = limit) })
        }
    }

    /** Массовый обход NFT-коллекций: собираем владельцев почти всех NFT. */
    fun startNftChain(requests: Int, onDone: (Result<Job>) -> Unit) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch {
            onDone(runCatching { client.startNftChain(requests = requests) })
        }
    }

    fun crawlGifts(
        query: String,
        depth: Int,
        maxProfiles: Int,
        onDone: (Result<Job>) -> Unit,
    ) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch {
            onDone(runCatching { client.crawlGifts(query, depth, maxProfiles) })
        }
    }

    // ── профиль цели ─────────────────────────────────────────────────────────

    fun refreshProfile(tgId: Long, onDone: (Result<Target>) -> Unit) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        if (profileBusy) return
        profileBusy = true
        viewModelScope.launch {
            val result = runCatching { client.refreshProfile(tgId) }
            result.onSuccess { fresh ->
                targets = targets.map { if (it.tgId == tgId) fresh else it }
            }
            profileBusy = false
            onDone(result)
        }
    }

    // ── сбор базы ────────────────────────────────────────────────────────────

    fun startBootstrap() {
        val client = api ?: run {
            bootError = "Движок не запущен"
            return
        }
        if (bootRunning || bootStarting) return
        bootStarting = true
        bootError = null
        bootJob = null
        viewModelScope.launch {
            runCatching { client.bootstrap() }
                .onSuccess { job ->
                    bootJob = job
                    pollBootstrap(client, job.key)
                }
                .onFailure { bootError = it.message ?: "Не удалось запустить сбор" }
            bootStarting = false
        }
    }

    fun cancelBootstrap() {
        val client = api ?: return
        val key = bootJob?.key ?: return
        viewModelScope.launch {
            runCatching { client.cancelJob(key) }
            bootJob = null
            bootError = null
        }
    }

    private suspend fun pollBootstrap(client: Api, key: String) {
        while (true) {
            delay(2_500)
            val fresh = runCatching { client.jobs().firstOrNull { it.key == key } }.getOrNull() ?: return
            bootJob = fresh
            if (fresh.finished) {
                bootError = fresh.error
                refresh(force = true)
                return
            }
        }
    }

    // ── вход в Telegram ──────────────────────────────────────────────────────

    fun startLogin(phone: String, onDone: (Result<LoginFlow>) -> Unit) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch { onDone(runCatching { client.startLogin(phone) }) }
    }

    fun submitLoginCode(id: String, code: String, onDone: (Result<LoginFlow>) -> Unit) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch {
            val result = runCatching { client.submitLoginCode(id, code) }
            result.onSuccess { if (it.done) afterLogin() }
            onDone(result)
        }
    }

    fun submitLoginPassword(id: String, password: String, onDone: (Result<LoginFlow>) -> Unit) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch {
            val result = runCatching { client.submitLoginPassword(id, password) }
            result.onSuccess { if (it.done) afterLogin() }
            onDone(result)
        }
    }

    fun cancelLogin(id: String) {
        val client = api ?: return
        viewModelScope.launch { runCatching { client.cancelLogin(id) } }
    }

    fun deleteSession(name: String, onDone: (Result<Unit>) -> Unit) {
        val client = api ?: run {
            onDone(Result.failure(ApiException(0, "Движок не запущен")))
            return
        }
        viewModelScope.launch {
            val result = runCatching { client.deleteSession(name) }
            result.onSuccess {
                sessions = sessions.filterNot { it.name == name }
                refresh(force = true)
            }
            onDone(result)
        }
    }

    private fun afterLogin() {
        refreshSessions()
        refresh(force = true)
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
        const val KEY_MD3 = "material3"
        const val KEY_CURRENCY = "display_currency"
        const val LOCAL_URL = "http://127.0.0.1:8080/"
        const val LOCAL_HOST = "http://127.0.0.1"
    }
}
