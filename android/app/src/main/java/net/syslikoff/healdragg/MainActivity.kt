package net.syslikoff.healdragg

import android.Manifest
import android.annotation.SuppressLint
import android.content.ClipData
import android.content.ClipboardManager
import android.content.ContentUris
import android.content.ContentValues
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
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
import androidx.annotation.RequiresApi
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Healdragg для Android — монитор публичных чатов Telegram.
 *
 * Движок (python-пакет healdragg + веб-панель) лежит внутри APK зашифрованным
 * и разворачивается на телефоне одним нажатием: GitHub и ПК не нужны.
 * Ключи Telegram остаются только на устройстве.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var web: WebView
    private lateinit var refresh: SwipeRefreshLayout
    private lateinit var progress: ProgressBar
    private lateinit var setup: ScrollView
    private lateinit var urlInput: EditText
    private lateinit var tokenInput: EditText
    private lateinit var termuxApiId: EditText
    private lateinit var termuxApiHash: EditText
    private lateinit var termuxBotToken: EditText
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
                        openPanel()
                    }
                    awaitingEngine && pollAttempts < 300 -> {
                        if (pollAttempts == 60) engineStatus.text = getString(R.string.engine_slow)
                        handler.postDelayed(this, 2_000)
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
        termuxApiId = findViewById(R.id.termuxApiId)
        termuxApiHash = findViewById(R.id.termuxApiHash)
        termuxBotToken = findViewById(R.id.termuxBotToken)
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

        urlInput.setText(prefs.getString(KEY_URL, ""))
        tokenInput.setText(prefs.getString(KEY_TOKEN, ""))
        termuxApiId.setText(prefs.getString(KEY_API_ID, ""))
        termuxApiHash.setText(prefs.getString(KEY_API_HASH, ""))
        termuxBotToken.setText(prefs.getString(KEY_BOT_TOKEN, ""))

        val storedUrl = prefs.getString(KEY_URL, "").orEmpty()
        if (storedUrl.isBlank()) {
            showSetup()
        } else if (storedUrl == LOCAL_URL) {
            // движок мог ещё ставиться — ждём его и открываем панель сами
            showSetup()
            pingEngine { alive ->
                if (alive) {
                    openPanel()
                } else {
                    awaitingEngine = true
                    startPolling()
                }
            }
        } else {
            openPanel()
        }
    }

    private fun setupUi() {
        findViewById<Button>(R.id.termuxCopyBtn).setOnClickListener { startEngine() }
        findViewById<Button>(R.id.termuxSaveBtn).setOnClickListener { saveTermuxScript() }
        findViewById<Button>(R.id.termuxOpenBtn).setOnClickListener { openTermux() }
        findViewById<Button>(R.id.localBtn).setOnClickListener {
            urlInput.setText(LOCAL_URL)
            tokenInput.setText(prefs.getString(KEY_TOKEN, ""))
            pingEngine { alive ->
                if (alive) {
                    prefs.edit().putString(KEY_URL, LOCAL_URL).apply()
                    openPanel()
                } else {
                    toast(R.string.engine_off)
                    refreshEngineStatus()
                }
            }
        }
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
    }

    // ── движок внутри APK ────────────────────────────────────────────────────

    /** Один тап: распаковать движок, положить скрипт, открыть Termux. */
    private fun startEngine() {
        if (!termuxReady()) return
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.WRITE_EXTERNAL_STORAGE),
                REQ_STORAGE,
            )
            return
        }
        if (!termuxInstalled()) {
            toast(R.string.termux_install_first)
            openExternal(TERMUX_URL)
            return
        }

        val apiId = termuxApiId.text.toString().trim()
        val apiHash = termuxApiHash.text.toString().trim()
        val botToken = termuxBotToken.text.toString().trim()
        val engineToken = ensureToken()
        prefs.edit()
            .putString(KEY_URL, LOCAL_URL)
            .putString(KEY_TOKEN, engineToken)
            .putString(KEY_API_ID, apiId)
            .putString(KEY_API_HASH, apiHash)
            .putString(KEY_BOT_TOKEN, botToken)
            .apply()

        engineStatus.text = getString(R.string.engine_starting)
        toast(R.string.engine_preparing)
        Thread {
            try {
                val tarPath = writeDownloads(ENGINE_NAME, "application/gzip", readEngine())
                    ?: throw IOException("нет доступа к Downloads")
                val script = termuxScript(
                    tarPath,
                    BuildConfig.ENGINE_ID,
                    apiId,
                    apiHash,
                    botToken,
                    engineToken,
                )
                val scriptPath = writeDownloads(SCRIPT_NAME, "text/x-shellscript", script.toByteArray())
                    ?: throw IOException("нет доступа к Downloads")
                runOnUiThread {
                    copyToClipboard(script)
                    val autoRun = launchTermuxScript(scriptPath)
                    awaitingEngine = true
                    startPolling()
                    openTermux()
                    toast(if (autoRun) R.string.engine_running else R.string.engine_paste)
                }
            } catch (exc: Exception) {
                runOnUiThread { toast("Не удалось подготовить движок: ${exc.message}") }
            }
        }.start()
    }

    /** Скрипт для Termux: локальный архив, один раз установить и держать движок в фоне. */
    private fun termuxScript(
        tarPath: String,
        engineId: String,
        apiId: String,
        apiHash: String,
        botToken: String,
        engineToken: String,
    ): String = """
        set -e
        trap 'termux-toast "Healdragg: установка не удалась — открой Termux" 2>/dev/null || true' ERR
        termux-setup-storage >/dev/null 2>&1 || true
        ENGINE_ID="@@ENGINE_ID@@"
        DIR="${'$'}HOME/healdragg"
        mkdir -p "${'$'}DIR"
        cd "${'$'}DIR"
        if [ -f "@@TAR@@" ]; then
          echo "🩺 Распаковываю движок из APK"
          tar -xzf "@@TAR@@" -C .
          rm -f "@@TAR@@"
        fi
        if [ ! -x "${'$'}DIR/.venv/bin/healdragg" ] || [ "${'$'}(cat "${'$'}DIR/.engine-id" 2>/dev/null)" != "${'$'}ENGINE_ID" ]; then
          echo "🩺 Ставлю движок — это один раз и несколько минут"
          bash install.sh
          echo "${'$'}ENGINE_ID" > "${'$'}DIR/.engine-id"
        fi
        cat > .env <<'EOF'
        API_ID=@@API_ID@@
        API_HASH=@@API_HASH@@
        BOT_TOKEN=@@BOT_TOKEN@@
        API_TOKEN=@@TOKEN@@
        API_HOST=127.0.0.1
        AUTOPILOT_MINUTES=15
        AUTO_ACTIVITY_ON_SCAN=true
        ALERT_PUSH=true
        EOF
        if [ -f "${'$'}DIR/data/api.pid" ] && kill -0 "${'$'}(cat "${'$'}DIR/data/api.pid")" 2>/dev/null; then
          kill "${'$'}(cat "${'$'}DIR/data/api.pid")" 2>/dev/null || true
          sleep 1
        fi
        termux-wake-lock >/dev/null 2>&1 || true
        mkdir -p "${'$'}DIR/data/logs"
        nohup "${'$'}DIR/.venv/bin/healdragg" api --host 127.0.0.1 --port 8080 \
          >>"${'$'}DIR/data/logs/api.log" 2>&1 &
        echo ${'$'}! > "${'$'}DIR/data/api.pid"
        if [ -d "${'$'}HOME/.termux/boot" ]; then
          printf '#!/data/data/com.termux/files/usr/bin/sh\ntermux-wake-lock\nexec "%s" api --host 127.0.0.1 --port 8080\n' \
            "${'$'}DIR/.venv/bin/healdragg" > "${'$'}HOME/.termux/boot/healdragg.sh"
          chmod +x "${'$'}HOME/.termux/boot/healdragg.sh"
        fi
        echo "🟢 Панель запущена: http://127.0.0.1:8080"
        echo "   лог: ${'$'}DIR/data/logs/api.log"
    """.trimIndent()
        .replace("@@TAR@@", tarPath)
        .replace("@@ENGINE_ID@@", engineId)
        .replace("@@API_ID@@", apiId)
        .replace("@@API_HASH@@", apiHash)
        .replace("@@BOT_TOKEN@@", botToken)
        .replace("@@TOKEN@@", engineToken)

    private fun readEngine(): ByteArray {
        val raw = assets.open(ENGINE_ASSET).readBytes()
        require(raw.size > 28) { "движок не встроен в эту сборку" }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            Cipher.DECRYPT_MODE,
            SecretKeySpec(engineKey(), "AES"),
            GCMParameterSpec(128, raw, 0, 12),
        )
        return cipher.doFinal(raw, 12, raw.size - 12)
    }

    private fun engineKey(): ByteArray = MessageDigest.getInstance("SHA-256")
        .digest(listOf("heald", "ragg", "-engine", "-v1").joinToString("").toByteArray())

    private fun ensureToken(): String {
        val saved = prefs.getString(KEY_TOKEN, "").orEmpty()
        if (saved.length >= 16) return saved
        val bytes = ByteArray(18).also { SecureRandom().nextBytes(it) }
        val token = bytes.joinToString("") { "%02x".format(it) }
        prefs.edit().putString(KEY_TOKEN, token).apply()
        return token
    }

    private fun writeDownloads(name: String, mime: String, bytes: ByteArray): String? {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            if (!writeDownloadsQ(name, mime, bytes)) return null
        } else {
            @Suppress("DEPRECATION")
            val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            if (!dir.exists() && !dir.mkdirs()) return null
            File(dir, name).writeBytes(bytes)
        }
        @Suppress("DEPRECATION")
        return File(Environment.getExternalStorageDirectory(), "Download/$name").absolutePath
    }

    @RequiresApi(Build.VERSION_CODES.Q)
    private fun writeDownloadsQ(name: String, mime: String, bytes: ByteArray): Boolean {
        val collection = MediaStore.Downloads.EXTERNAL_CONTENT_URI
        contentResolver.query(
            collection,
            arrayOf(MediaStore.Downloads._ID),
            "${MediaStore.Downloads.DISPLAY_NAME}=?",
            arrayOf(name),
            null,
        )?.use { cursor ->
            while (cursor.moveToNext()) {
                val id = cursor.getLong(0)
                contentResolver.delete(ContentUris.withAppendedId(collection, id), null, null)
            }
        }
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, name)
            put(MediaStore.Downloads.MIME_TYPE, mime)
            put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val uri = contentResolver.insert(collection, values) ?: return false
        contentResolver.openOutputStream(uri)?.use { it.write(bytes) } ?: return false
        values.clear()
        values.put(MediaStore.Downloads.IS_PENDING, 0)
        contentResolver.update(uri, values, null, null)
        return true
    }

    /** Termux RUN_COMMAND: запустить скрипт, даже не открывая клавиатуру. */
    private fun launchTermuxScript(path: String): Boolean {
        val intent = Intent("com.termux.RUN_COMMAND").apply {
            setClassName("com.termux", "com.termux.app.RunCommandService")
            putExtra("com.termux.RUN_COMMAND_PATH", TERMUX_BASH)
            putExtra("com.termux.RUN_COMMAND_ARGUMENTS", arrayOf(path))
            putExtra("com.termux.RUN_COMMAND_WORKDIR", TERMUX_HOME)
            putExtra("com.termux.RUN_COMMAND_BACKGROUND", false)
            putExtra("com.termux.RUN_COMMAND_SESSION_ACTION", "0")
        }
        return try {
            startService(intent)
            true
        } catch (_: Exception) {
            false
        }
    }

    private fun termuxInstalled(): Boolean = try {
        packageManager.getPackageInfo(TERMUX_PACKAGE, 0)
        true
    } catch (_: PackageManager.NameNotFoundException) {
        false
    }

    private fun termuxReady(): Boolean {
        if (termuxApiId.text.isBlank() || termuxApiHash.text.isBlank()) {
            toast(R.string.termux_need)
            return false
        }
        return true
    }

    private fun saveTermuxScript() {
        if (!termuxReady()) return
        val token = ensureToken()
        engineStatus.text = getString(R.string.engine_starting)
        toast(R.string.engine_preparing)
        Thread {
            try {
                val tarPath = writeDownloads(ENGINE_NAME, "application/gzip", readEngine())
                    ?: throw IOException("нет доступа к Downloads")
                val script = termuxScript(
                    tarPath,
                    BuildConfig.ENGINE_ID,
                    termuxApiId.text.toString().trim(),
                    termuxApiHash.text.toString().trim(),
                    termuxBotToken.text.toString().trim(),
                    token,
                )
                val path = writeDownloads(SCRIPT_NAME, "text/x-shellscript", script.toByteArray())
                    ?: throw IOException("нет доступа к Downloads")
                runOnUiThread { toast(getString(R.string.termux_saved, path)) }
            } catch (exc: Exception) {
                runOnUiThread { toast("Не удалось сохранить: ${exc.message}") }
            }
        }.start()
    }

    private fun copyToClipboard(text: String) {
        try {
            val clipboard = getSystemService(ClipboardManager::class.java)
            clipboard?.setPrimaryClip(ClipData.newPlainText("healdragg setup", text))
        } catch (_: Exception) {
            // буфер обмена может быть недоступен — скрипт всё равно лежит в Downloads
        }
    }

    // ── статус и опрос движка ────────────────────────────────────────────────

    private fun startPolling() {
        pollAttempts = 0
        handler.removeCallbacks(pollTask)
        handler.postDelayed(pollTask, 2_500)
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
        pingEngine { alive ->
            engineStatus.text = getString(if (alive) R.string.engine_on else R.string.engine_off)
            engineStatus.setTextColor(Color.parseColor(if (alive) "#6EE7A8" else "#8BA1BF"))
            if (alive && awaitingEngine) {
                awaitingEngine = false
                toast(R.string.engine_ready)
                openPanel()
            }
        }
    }

    private fun openTermux() {
        val intent = Intent().apply {
            setClassName(TERMUX_PACKAGE, "com.termux.app.TermuxActivity")
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        try {
            startActivity(intent)
        } catch (_: Exception) {
            openExternal(TERMUX_URL)
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
                if (request.isForMainFrame) {
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
        termuxApiId.setText(prefs.getString(KEY_API_ID, ""))
        termuxApiHash.setText(prefs.getString(KEY_API_HASH, ""))
        termuxBotToken.setText(prefs.getString(KEY_BOT_TOKEN, ""))
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
        web.loadUrl(url)
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

    private fun toast(text: String) {
        Toast.makeText(this, text, Toast.LENGTH_LONG).show()
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_STORAGE) {
            if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) startEngine()
            else toast(R.string.storage_denied)
        }
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
        private const val KEY_BOT_TOKEN = "tg_bot_token"
        private const val LOCAL_URL = "http://127.0.0.1:8080/"
        private const val ENGINE_ASSET = "engine.bin"
        private const val ENGINE_NAME = "healdragg-engine.tar.gz"
        private const val SCRIPT_NAME = "healdragg-termux.sh"
        private const val TERMUX_PACKAGE = "com.termux"
        private const val TERMUX_BASH = "/data/data/com.termux/files/usr/bin/bash"
        private const val TERMUX_HOME = "/data/data/com.termux/files/home"
        private const val TERMUX_URL = "https://f-droid.org/packages/com.termux/"
        private const val REQ_STORAGE = 41
    }
}
