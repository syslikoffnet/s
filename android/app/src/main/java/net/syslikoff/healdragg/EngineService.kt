package net.syslikoff.healdragg

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File

/**
 * Фоновый движок Healdragg: Python-панель живёт прямо в процессе приложения.
 *
 * Termux больше не нужен — Chaquopy поднимает CPython внутри APK, uvicorn
 * слушает 127.0.0.1:8080, а нативные Compose-экраны читают это же REST API.
 */
class EngineService : Service() {

    private val prefs by lazy { getSharedPreferences(PREFS, MODE_PRIVATE) }
    @Volatile
    private var worker: Thread? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        ensureChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            shutdown()
            stopForegroundCompat()
            stopSelf()
            return START_NOT_STICKY
        }

        startForegroundCompat(getString(R.string.engine_starting))

        val apiId = prefs.getString(KEY_API_ID, "").orEmpty().trim()
        val apiHash = prefs.getString(KEY_API_HASH, "").orEmpty().trim()
        if (apiId.isBlank() || apiHash.isBlank()) {
            prefs.edit().putString(KEY_STATE, STATE_NEED_CREDS).apply()
            stopForegroundCompat()
            stopSelf()
            return START_NOT_STICKY
        }

        if (worker?.isAlive != true) {
            prefs.edit().putString(KEY_STATE, STATE_STARTING).remove(KEY_ERROR).apply()
            // Python.start на главном потоке: так первая распаковка stdlib не
            // рискует уронить фоновый поток и гарантированно идёт после startForeground.
            try {
                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(applicationContext))
                }
            } catch (exc: Throwable) {
                val message = exc.message?.take(400) ?: exc.toString().take(400)
                prefs.edit()
                    .putString(KEY_STATE, STATE_ERROR)
                    .putString(KEY_ERROR, message)
                    .apply()
                updateNotification(getString(R.string.engine_failed))
                stopForegroundCompat()
                stopSelf()
                return START_NOT_STICKY
            }
            worker = Thread({ runEngine(apiId, apiHash) }, "healdragg-engine-start").also { it.start() }
        }
        return START_STICKY
    }

    private fun runEngine(apiId: String, apiHash: String) {
        try {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(applicationContext))
            }
            val home = File(filesDir, "engine").apply { mkdirs() }
            val module = Python.getInstance().getModule("engine_main")
            module.callAttr(
                "start",
                home.absolutePath,
                apiId,
                apiHash,
                ensureToken(),
                PORT,
            )
            prefs.edit().putString(KEY_STATE, STATE_READY).apply()
            updateNotification(getString(R.string.engine_on))
            // Копия в «Загрузки»: она переживёт даже удаление приложения.
            val backupThread = Thread {
                Thread.sleep(20_000)
                runCatching { AutoBackup.maybeExport(applicationContext) }
            }
            backupThread.name = "healdragg-autobackup-start"
            backupThread.start()
        } catch (exc: Throwable) {
            val message = exc.message?.take(400) ?: exc.toString().take(400)
            prefs.edit()
                .putString(KEY_STATE, STATE_ERROR)
                .putString(KEY_ERROR, message)
                .apply()
            updateNotification(getString(R.string.engine_failed))
            shutdown()
            stopForegroundCompat()
            stopSelf()
        }
    }

    private fun shutdown() {
        try {
            if (Python.isStarted()) {
                Python.getInstance().getModule("engine_main").callAttr("stop")
            }
        } catch (_: Throwable) {
            // движок мог не успеть стартовать — молча гасим
        }
        prefs.edit().putString(KEY_STATE, STATE_STOPPED).apply()
    }

    private fun ensureToken(): String {
        val saved = prefs.getString(KEY_TOKEN, "").orEmpty()
        if (saved.length >= 16) return saved
        val bytes = ByteArray(18).also { java.security.SecureRandom().nextBytes(it) }
        val token = bytes.joinToString("") { "%02x".format(it) }
        prefs.edit().putString(KEY_TOKEN, token).apply()
        return token
    }

    override fun onDestroy() {
        shutdown()
        super.onDestroy()
    }

    // ── уведомление ──────────────────────────────────────────────────────────

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL) != null) return
        val channel = NotificationChannel(
            CHANNEL,
            getString(R.string.engine_channel),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = getString(R.string.engine_channel_desc)
            setShowBadge(false)
        }
        manager.createNotificationChannel(channel)
    }

    private fun buildNotification(text: String): Notification {
        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this,
            1,
            Intent(this, EngineService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(open)
            .addAction(0, getString(R.string.engine_stop), stop)
            .build()
    }

    private fun startForegroundCompat(text: String) {
        val notification = buildNotification(text)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIF_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIF_ID, notification)
        }
    }

    private fun updateNotification(text: String) {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        manager.notify(NOTIF_ID, buildNotification(text))
    }

    private fun stopForegroundCompat() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }
    }

    companion object {
        const val ACTION_START = "net.syslikoff.healdragg.ENGINE_START"
        const val ACTION_STOP = "net.syslikoff.healdragg.ENGINE_STOP"
        const val PORT = 8080

        private const val CHANNEL = "engine"
        private const val NOTIF_ID = 42
        private const val PREFS = "healdragg"
        private const val KEY_API_ID = "tg_api_id"
        private const val KEY_API_HASH = "tg_api_hash"
        private const val KEY_TOKEN = "api_token"
        private const val KEY_STATE = "engine_state"
        private const val KEY_ERROR = "engine_error"
        const val STATE_STARTING = "starting"
        const val STATE_READY = "ready"
        const val STATE_STOPPED = "stopped"
        const val STATE_ERROR = "error"
        const val STATE_NEED_CREDS = "need_creds"

        fun start(context: android.content.Context) {
            val intent = Intent(context, EngineService::class.java).setAction(ACTION_START)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: android.content.Context) {
            context.startService(Intent(context, EngineService::class.java).setAction(ACTION_STOP))
        }
    }
}
