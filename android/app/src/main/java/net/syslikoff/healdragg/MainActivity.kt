package net.syslikoff.healdragg

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.appcompat.app.AppCompatActivity
import androidx.compose.runtime.LaunchedEffect
import androidx.core.content.ContextCompat
import net.syslikoff.healdragg.ui.AppViewModel
import net.syslikoff.healdragg.ui.EngineState
import net.syslikoff.healdragg.ui.HealdraggApp
import net.syslikoff.healdragg.ui.HealdraggTheme

/**
 * Healdragg для Android — нативный монитор публичных чатов Telegram.
 *
 * UI целиком на Compose, движок (CPython + uvicorn) живёт в foreground-сервисе
 * на 127.0.0.1:8080. Termux и браузер не участвуют.
 */
class MainActivity : AppCompatActivity() {

    private val vm: AppViewModel by viewModels()

    private val notifyPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            HealdraggTheme(md3 = vm.md3) {
                LaunchedEffect(Unit) {
                    maybeAskNotifications()
                    AlertsWork.schedule(this@MainActivity)
                    if (vm.isLocal && vm.hasCredentials && vm.engine != EngineState.Ready) {
                        // движок поднимаем сами, даже если приложение только что открыли
                        runCatching { vm.startEngine() }
                    }
                }
                HealdraggApp(
                    vm = vm,
                    onOpenBattery = { openBatterySettings() },
                )
            }
        }
    }

    override fun onResume() {
        super.onResume()
        // после возврата из настроек батареи обновим данные
        runCatching { vm.refresh() }
    }

    private fun maybeAskNotifications() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        ) return
        val prefs = getSharedPreferences(AppViewModel.PREFS, MODE_PRIVATE)
        if (prefs.getBoolean(KEY_ASKED_NOTIFICATIONS, false)) return
        prefs.edit().putBoolean(KEY_ASKED_NOTIFICATIONS, true).apply()
        notifyPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
    }

    private companion object {
        const val KEY_ASKED_NOTIFICATIONS = "asked_notifications"
    }

    private fun openBatterySettings() {
        try {
            startActivity(
                Intent(
                    Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:$packageName"),
                ),
            )
        } catch (_: Exception) {
            startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        }
    }
}
