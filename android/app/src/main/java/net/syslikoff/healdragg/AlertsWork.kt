package net.syslikoff.healdragg

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.TimeUnit

/** Периодическая проверка алертов движка: уведомления приходят, даже если APK закрыт. */
object AlertsWork {
    private const val UNIQUE = "healdragg-alerts"

    fun schedule(context: Context) {
        val request = PeriodicWorkRequestBuilder<AlertWorker>(15, TimeUnit.MINUTES).build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            UNIQUE,
            ExistingPeriodicWorkPolicy.UPDATE,
            request,
        )
    }
}

class AlertWorker(context: Context, params: WorkerParameters) : Worker(context, params) {
    override fun doWork(): Result {
        val prefs = applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val base = prefs.getString(KEY_URL, "").orEmpty()
        if (base.isBlank()) return Result.success()
        val token = prefs.getString(KEY_TOKEN, "").orEmpty()
        val payload = try {
            getJson(base + "api/alerts?limit=5&unread=true", token)
        } catch (_: Exception) {
            null
        } ?: return Result.success()

        val unread = payload.optInt("unread", 0)
        if (unread == 0) {
            prefs.edit().putInt(KEY_UNREAD, 0).apply()
            return Result.success()
        }
        if (prefs.getInt(KEY_UNREAD, -1) == unread) return Result.success()

        val first = payload.optJSONArray("rows")?.optJSONObject(0)
        val title = first?.optString("title").orEmpty().ifBlank { "🔔 Новые алерты" }
        val body = first?.optString("body").orEmpty().ifBlank { "Непрочитанных: $unread" }
        if (Notifications.show(applicationContext, title, body)) {
            prefs.edit().putInt(KEY_UNREAD, unread).apply()
        }
        return Result.success()
    }

    private fun getJson(url: String, token: String): JSONObject? {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.connectTimeout = 3_000
        conn.readTimeout = 3_000
        if (token.isNotEmpty()) conn.setRequestProperty("X-Api-Token", token)
        return try {
            if (conn.responseCode !in 200..299) {
                null
            } else {
                JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
            }
        } finally {
            conn.disconnect()
        }
    }

    private companion object {
        const val PREFS = "healdragg"
        const val KEY_URL = "base_url"
        const val KEY_TOKEN = "api_token"
        const val KEY_UNREAD = "notified_unread"
    }
}

object Notifications {
    private const val CHANNEL = "healdragg-alerts"

    fun show(context: Context, title: String, text: String): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            return false
        }
        val manager = context.getSystemService(NotificationManager::class.java) ?: return false
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && manager.getNotificationChannel(CHANNEL) == null) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL, "Алерты Healdragg", NotificationManager.IMPORTANCE_HIGH),
            )
        }
        val intent = Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val pending = PendingIntent.getActivity(
            context,
            0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notify)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setAutoCancel(true)
            .setContentIntent(pending)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()
        return try {
            manager.notify(ALERT_ID, notification)
            true
        } catch (_: SecurityException) {
            false
        }
    }

    private const val ALERT_ID = 77
}
