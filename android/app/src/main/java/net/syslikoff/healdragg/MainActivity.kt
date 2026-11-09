package net.syslikoff.healdragg

import android.annotation.SuppressLint
import android.content.ClipData
import android.content.ClipboardManager
import android.content.ContentValues
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
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
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import java.io.File

/**
 * Healdragg для Android — компактный клиент веб-панели.
 *
 * Приложение не хранит ключи Telegram и не работает с API напрямую:
 * оно открывает вашу self-hosted панель (healdragg api) и запоминает адрес.
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
    private lateinit var settingsBtn: View

    private val prefs by lazy { getSharedPreferences(PREFS, MODE_PRIVATE) }
    private var lastBackAt = 0L

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

        if (prefs.getString(KEY_URL, "").isNullOrBlank()) showSetup() else openPanel()
    }

    private fun setupUi() {
        findViewById<Button>(R.id.termuxCopyBtn).setOnClickListener { copyTermuxScript() }
        findViewById<Button>(R.id.termuxSaveBtn).setOnClickListener { saveTermuxScript() }
        findViewById<Button>(R.id.termuxOpenBtn).setOnClickListener { openTermux() }
        findViewById<Button>(R.id.localBtn).setOnClickListener {
            urlInput.setText(LOCAL_URL)
            Toast.makeText(this, R.string.local_hint, Toast.LENGTH_LONG).show()
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

    /** Скрипт, который ставит движок и запускает панель прямо на телефоне. */
    private fun termuxScript(): String {
        val apiId = termuxApiId.text.toString().trim()
        val apiHash = termuxApiHash.text.toString().trim()
        val botToken = termuxBotToken.text.toString().trim()
        val env = StringBuilder()
        env.appendLine("cat > .env <<'EOF'")
        env.appendLine("API_ID=$apiId")
        env.appendLine("API_HASH=$apiHash")
        if (botToken.isNotEmpty()) env.appendLine("BOT_TOKEN=$botToken")
        env.appendLine("AUTOPILOT_MINUTES=15")
        env.appendLine("AUTO_ACTIVITY_ON_SCAN=true")
        env.appendLine("ALERT_PUSH=true")
        env.appendLine("EOF")
        return """
            set -e
            pkg update -y
            pkg install -y git python
            if [ ! -d "${'$'}HOME/healdragg" ]; then
              git clone --depth 1 https://github.com/syslikoffnet/s.git "${'$'}HOME/healdragg"
            fi
            cd "${'$'}HOME/healdragg"
            ${env.toString().trimEnd()}
            bash install.sh
            healdragg api --host 0.0.0.0 --port 8080
        """.trimIndent()
    }

    private fun termuxReady(): Boolean {
        if (termuxApiId.text.isBlank() || termuxApiHash.text.isBlank()) {
            Toast.makeText(this, R.string.termux_need, Toast.LENGTH_LONG).show()
            return false
        }
        return true
    }

    private fun copyTermuxScript() {
        if (!termuxReady()) return
        try {
            val clipboard = getSystemService(ClipboardManager::class.java)
            clipboard?.setPrimaryClip(ClipData.newPlainText("healdragg setup", termuxScript()))
            Toast.makeText(this, R.string.termux_copied, Toast.LENGTH_LONG).show()
        } catch (exc: Exception) {
            Toast.makeText(this, "Не удалось скопировать: ${exc.message}", Toast.LENGTH_LONG).show()
        }
    }

    private fun saveTermuxScript() {
        if (!termuxReady()) return
        val text = termuxScript()
        val name = "healdragg-termux.sh"
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                val values = ContentValues().apply {
                    put(MediaStore.Downloads.DISPLAY_NAME, name)
                    put(MediaStore.Downloads.MIME_TYPE, "text/x-shellscript")
                }
                val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                if (uri != null) {
                    contentResolver.openOutputStream(uri)?.use { stream ->
                        stream.write(text.toByteArray())
                    }
                    Toast.makeText(
                        this,
                        getString(R.string.termux_saved, "Downloads/$name"),
                        Toast.LENGTH_LONG,
                    ).show()
                    return
                }
            }
            val file = File(getExternalFilesDir(null), name)
            file.writeText(text)
            Toast.makeText(
                this,
                getString(R.string.termux_saved, file.absolutePath),
                Toast.LENGTH_LONG,
            ).show()
        } catch (exc: Exception) {
            Toast.makeText(this, "Не удалось сохранить: ${exc.message}", Toast.LENGTH_LONG).show()
        }
    }

    private fun openTermux() {
        val intent = Intent().apply {
            setClassName("com.termux", "com.termux.app.TermuxActivity")
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        try {
            startActivity(intent)
        } catch (exc: Exception) {
            openExternal("https://f-droid.org/packages/com.termux/")
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView() {
        web.setBackgroundColor(Color.parseColor("#0E0E15"))
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
                error: WebResourceError
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

        refresh.setColorSchemeColors(Color.parseColor("#BD93F9"), Color.parseColor("#8BE9FD"))
        refresh.setProgressBackgroundColorSchemeColor(Color.parseColor("#1E1E2E"))
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

    override fun onPause() {
        super.onPause()
        web.onPause()
    }

    override fun onResume() {
        super.onResume()
        web.onResume()
    }

    companion object {
        private const val PREFS = "healdragg"
        private const val KEY_URL = "base_url"
        private const val KEY_TOKEN = "api_token"
        private const val LOCAL_URL = "http://127.0.0.1:8080/"
    }
}
