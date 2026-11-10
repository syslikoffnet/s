package net.syslikoff.healdragg

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** После перезагрузки и обновления APK движок поднимается сам, если настроен. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED &&
            intent.action != Intent.ACTION_MY_PACKAGE_REPLACED
        ) {
            return
        }
        val prefs = context.getSharedPreferences("healdragg", Context.MODE_PRIVATE)
        val apiId = prefs.getString("tg_api_id", "").orEmpty()
        val apiHash = prefs.getString("tg_api_hash", "").orEmpty()
        if (apiId.isBlank() || apiHash.length < 16) return
        try {
            EngineService.start(context)
        } catch (_: Exception) {
            // Android может запретить старт из фона — тогда движок поднимется при открытии
        }
    }
}
