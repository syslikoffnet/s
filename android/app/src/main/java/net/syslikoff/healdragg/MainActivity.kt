package net.syslikoff.healdragg

import android.Manifest
import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import android.text.method.ScrollingMovementMethod
import android.view.View
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Healdragg для Android — монитор публичных чатов Telegram.
 *
 * Движок встроен в APK: Chaquopy поднимает CPython, uvicorn отдаёт панель на
 * 127.0.0.1:8080. Termux, pip и Rust на телефоне не участвуют.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var web: WebView
    private lateinit var refresh: SwipeRefreshLayout
    private lateinit var progress: ProgressBar
    private lateinit var setup: ScrollView
    private lateinit var urlInput: EditText
    private lateinit var tokenInput: EditText
    private lateinit var apiIdInput: EditText
    private lateinit var apiHashInput: EditText
    private lateinit var engineStatus: TextView
    private lateinit var settingsBtn: View

    private val prefs by lazy { getSharedPreferences(PREFS, MODE_PRIVATE) }
    private val handler = Handler(Looper.getMainLooper())
    private var lastBackAt = 0L
    private var awaitingEngine = false
    private var pollAttempts = 0

    private val pollTask = object : Runnable {
        override fun run() {
            pollAttempts += 1
            pingEngine { alive ->
                when {
                    alive -> {
                        awaitingEngine = false
                        toast(R.string.engine_ready)
                        prefs.edit().putString(KEY_URL, LOCAL_URL).apply()
                        openPanel()
                    }
                    awaitingEngine && pollAttempts < 150 -> {
                        if (pollAttempts == 30) {
                            engineStatus.text = getString(R.string.engine_slow)
                        }
                        handler.postDelayed(this, 2_000)
                    }
                    awaitingEngine -> {
                        awaitingEngine = false
                        engineStatus.text = getString(R.string.engine_failed)
                        toast(R.string.engine_failed)
                    }
                }
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        WindowCompat.setDecorFitsSystemWindows(window, true)
        setContentView(R.layout.activity_main)

        web = findViewById(R.id.web)
        refresh = findViewById(R.id.refresh)
        progress = findViewById(R.id.progress)
        setup = findViewById(R.id.setup)
        urlInput = findViewById(R.id.urlInput)
        tokenInput = findViewById(R.id.tokenInput)
        apiIdInput = findViewById(R.id.apiIdInput)
        apiHashInput = findViewById(R.id.apiHashInput)
        engineStatus = findViewById(R.id.engineStatus)
        settingsBtn = findViewById(R.id.settingsBtn)
        settingsBtn.setOnClickListener { showSetup() }

        configureWebView()
        setupUi()

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                when {
                    setup.isShown -> finish()
                    web.canGoBack() -> web.goBack()
                    else -> {
                        val now = System.currentTimeMillis()
                        if (now - lastBackAt < 2000) finish()
                        else {
                            lastBackAt = now
                            Toast.makeText(this@MainActivity, R.string.back_again, Toast.LENGTH_SHORT).show()
                        }
                    }
                }
            }
        })

        apiIdInput.setText(prefs.getString(KEY_API_ID, ""))
        apiHashInput.setText(prefs.getString(KEY_API_HASH, ""))
        urlInput.setText(prefs.getString(KEY_URL, ""))
        tokenInput.setText(prefs.getString(KEY_TOKEN, ""))

        val storedUrl = prefs.getString(KEY_URL, "").orEmpty()
        if (storedUrl.isBlank() || storedUrl == LOCAL_URL) {
            showSetup()
            if (hasCredentials()) {
                startEngine()
            } else {
                refreshEngineStatus()
            }
        } else {
            openPanel()
        }
    }

    private fun setupUi() {
        findViewById<Button>(R.id.startBtn).setOnClickListener { startEngine() }
        findViewById<Button>(R.id.stopBtn).setOnClickListener {
            EngineService.stop(this)
            awaitingEngine = false
            handler.removeCallbacks(pollTask)
            toast(R.string.engine_stopped)
            handler.postDelayed({ refreshEngineStatus() }, 800)
        }
        findViewById<Button>(R.id.openBtn).setOnClickListener {
            ensureToken()
            prefs.edit().putString(KEY_URL, LOCAL_URL).apply()
            pingEngine { alive ->
                if (alive) openPanel() else {
                    toast(R.string.engine_off)
                    refreshEngineStatus()
                }
            }
        }
        findViewById<Button>(R.id.logBtn).setOnClickListener { showLog() }
        findViewById<Button>(R.id.batteryBtn).setOnClickListener { askBattery() }

        findViewById<Button>(R.id.connectBtn).setOnClickListener {
            val base = normalize(urlInput.text.toString())
            if (base.isBlank()) {
                Toast.makeText(this, R.string.error_url, Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            prefs.edit()
                .putString(KEY_URL, base)
                .putString(KEY_TOKEN, tokenInput.text.toString().trim())
                .apply()
            openPanel()
        }
        findViewById<Button>(R.id.localBtn).setOnClickListener {
            urlInput.setText(LOCAL_URL)
            prefs.edit().putString(KEY_URL, LOCAL_URL).apply()
            pingEngine { alive ->
                if (alive) openPanel() else {
                    toast(R.string.engine_off)
                    refreshEngineStatus()
                }
            }
        }
    }

    // ── движок внутри APK ────────────────────────────────────────────────────

    private fun hasCredentials(): Boolean {
        val apiId = prefs.getString(KEY_API_ID, "").orEmpty().trim()
        val apiHash = prefs.getString(KEY_API_HASH, "").orEmpty().trim()
        return apiId.isNotBlank() && apiHash.length >= 16
    }

    /** Один тап: сохранить ключи и поднять Python-движок в фоне. */
    private fun startEngine() {
        val apiId = apiIdInput.text.toString().trim()
        val apiHash = apiHashInput.text.toString().trim()
        if (apiId.isBlank() || apiHash.length < 16) {
            toast(R.string.engine_need_creds)
            return
        }
        prefs.edit()
            .putString(KEY_API_ID, apiId)
            .putString(KEY_API_HASH, apiHash)
            .putString(KEY_URL, LOCAL_URL)
            .apply()
        ensureToken()

        engineStatus.text = getString(R.string.engine_starting)
        engineStatus.setTextColor(Color.parseColor("#8BA1BF"))
        toast(R.string.engine_booting)
        awaitingEngine = true
        EngineService.start(this)
        startPolling()
    }

    private fun ensureToken(): String {
        val saved = prefs.getString(KEY_TOKEN, "").orEmpty()
        if (saved.length >= 16) return saved
        val bytes = ByteArray(18).also { java.security.SecureRandom().nextBytes(it) }
        val token = bytes.joinToString("") { "%02x".format(it) }
        prefs.edit().putString(KEY_TOKEN, token).apply()
        return token
    }

    private fun showLog() {
        val file = File(filesDir, "engine/logs/engine.log")
        val text = try {
            file.readLines().takeLast(400).joinToString("\n").ifBlank { "Лог пока пуст." }
        } catch (_: Exception) {
            "Лог пока пуст."
        }
        val view = TextView(this).apply {
            text = text
            setTextColor(Color.parseColor("#E8F0FF"))
            textSize = 11f
            setPadding(32, 24, 32, 24)
            movementMethod = ScrollingMovementMethod()
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.engine_log_title)
            .setView(view)
            .setPositiveButton(android.R.string.ok, null)
            .show()
    }

    private fun askBattery() {
        val manager = getSystemService(PowerManager::class.java)
        if (manager?.isIgnoringBatteryOptimizations(packageName) == true) {
            toast(R.string.battery_ok)
            return
        }
        try {
            startActivity(
                Intent(
                    Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:$packageName"),
                ),
            )
        } catch (_: Exception) {
            try {
                startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
            } catch (_: Exception) {
                toast(R.string.battery_manual)
            }
        }
    }

    // ── статус и опрос движка ────────────────────────────────────────────────

    private fun startPolling() {
        pollAttempts = 0
        handler.removeCallbacks(pollTask)
        handler.postDelayed(pollTask, 1_500)
    }

    private fun pingEngine(callback: (Boolean) -> Unit) {
        Thread {
            val alive = try {
                val conn = URL(LOCAL_URL + "api/health").openConnection() as HttpURLConnection
                conn.connectTimeout = 1_200
                conn.readTimeout = 1_200
                conn.requestMethod = "GET"
                val code = conn.responseCode
                conn.disconnect()
                code in 200..299
            } catch (_: Exception) {
                false
            }
            runOnUiThread { callback(alive) }
        }.start()
    }

    private fun refreshEngineStatus() {
        val state = prefs.getString(KEY_STATE, "").orEmpty()
        val error = prefs.getString(KEY_ERROR, "").orEmpty()
        pingEngine { alive ->
            val text = when {
                alive -> getString(R.string.engine_on)
                state == EngineService.STATE_ERROR && error.isNotBlank() ->
                    getString(R.string.engine_failed_why, error)
                state == EngineService.STATE_STARTING -> getString(R.string.engine_starting)
                else -> getString(R.string.engine_off)
            }
            engineStatus.text = text
            engineStatus.setTextColor(
                Color.parseColor(if (alive) "#6EE7A8" else "#8BA1BF"),
            )
            if (alive && awaitingEngine) {
                awaitingEngine = false
                toast(R.string.engine_ready)
                prefs.edit().putString(KEY_URL, LOCAL_URL).apply()
                openPanel()
            }
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView() {
        web.setBackgroundColor(Color.parseColor("#05070D"))
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            databaseEnabled = true
            loadsImagesAutomatically = true
            useWideViewPort = true
            loadWithOverviewMode = true
            builtInZoomControls = false
            displayZoomControls = false
            setSupportZoom(false)
            // офлайн-режим: если движок недоступен, WebView отдаёт кэш,
            // а JS-панель показывает последний снимок из localStorage
            cacheMode = WebSettings.LOAD_CACHE_ELSE_NETWORK
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            mediaPlaybackRequiresUserGesture = true
        }
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, true)

        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url.toString()
                val base = prefs.getString(KEY_URL, "").orEmpty()
                val host = Uri.parse(base).host
                return if (url.startsWith("http") && Uri.parse(url).host == host) {
                    false
                } else {
                    openExternal(url)
                    true
                }
            }

            override fun onPageFinished(view: WebView, url: String) {
                progress.visibility = View.GONE
                refresh.isRefreshing = false
            }

            override fun onReceivedError(
                view: WebView,
                request: WebResourceRequest,
                error: WebResourceError,
            ) {
                if (request.isMainFrame) {
                    progress.visibility = View.GONE
                    refresh.isRefreshing = false
                    Toast.makeText(this@MainActivity, R.string.error_load, Toast.LENGTH_LONG).show()
                }
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, newProgress: Int) {
                if (newProgress < 100) progress.visibility = View.VISIBLE
            }
        }

        refresh.setColorSchemeColors(Color.parseColor("#7C5CFF"), Color.parseColor("#22D3EE"))
        refresh.setProgressBackgroundColorSchemeColor(Color.parseColor("#0B1220"))
        refresh.setOnRefreshListener { web.reload() }
    }

    private fun openExternal(url: String) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (_: Exception) {
            Toast.makeText(this, url, Toast.LENGTH_SHORT).show()
        }
    }

    private fun showSetup() {
        setup.visibility = View.VISIBLE
        refresh.visibility = View.GONE
        progress.visibility = View.GONE
        settingsBtn.visibility = View.GONE
        urlInput.setText(prefs.getString(KEY_URL, ""))
        tokenInput.setText(prefs.getString(KEY_TOKEN, ""))
        apiIdInput.setText(prefs.getString(KEY_API_ID, ""))
        apiHashInput.setText(prefs.getString(KEY_API_HASH, ""))
        refreshEngineStatus()
    }

    private fun openPanel() {
        val base = prefs.getString(KEY_URL, "").orEmpty()
        if (base.isBlank()) {
            showSetup()
            return
        }
        val token = prefs.getString(KEY_TOKEN, "").orEmpty()
        val url = if (token.isBlank()) base else base + "?token=" + Uri.encode(token)
        setup.visibility = View.GONE
        refresh.visibility = View.VISIBLE
        progress.visibility = View.VISIBLE
        settingsBtn.visibility = View.VISIBLE
        AlertsWork.schedule(this)
        maybeAskNotifications()
        web.loadUrl(url)
    }

    private fun maybeAskNotifications() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        ) return
        if (prefs.getBoolean(KEY_NOTIFY_ASKED, false)) return
        prefs.edit().putBoolean(KEY_NOTIFY_ASKED, true).apply()
        ActivityCompat.requestPermissions(
            this,
            arrayOf(Manifest.permission.POST_NOTIFICATIONS),
            REQ_NOTIFY,
        )
    }

    private fun normalize(raw: String): String {
        var value = raw.trim()
        if (value.isEmpty()) return ""
        if (!value.startsWith("http://") && !value.startsWith("https://")) value = "http://$value"
        if (!value.endsWith("/")) value += "/"
        return value
    }

    private fun toast(resId: Int) {
        Toast.makeText(this, resId, Toast.LENGTH_LONG).show()
    }

    override fun onPause() {
        super.onPause()
        web.onPause()
    }

    override fun onResume() {
        super.onResume()
        web.onResume()
        if (setup.isShown) refreshEngineStatus()
    }

    override fun onDestroy() {
        handler.removeCallbacks(pollTask)
        super.onDestroy()
    }

    companion object {
        private const val PREFS = "healdragg"
        private const val KEY_URL = "base_url"
        private const val KEY_TOKEN = "api_token"
        private const val KEY_API_ID = "tg_api_id"
        private const val KEY_API_HASH = "tg_api_hash"
        private const val KEY_STATE = "engine_state"
        private const val KEY_ERROR = "engine_error"
        private const val KEY_NOTIFY_ASKED = "notify_asked"
        private const val LOCAL_URL = "http://127.0.0.1:8080/"
        private const val REQ_NOTIFY = 42
    }
}
