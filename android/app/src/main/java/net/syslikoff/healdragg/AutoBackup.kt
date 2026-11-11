package net.syslikoff.healdragg

import android.annotation.SuppressLint
import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Копия базы и аккаунтов в «Загрузки/Healdragg».
 *
 * Android удаляет внутренние файлы приложения при удалении, а «Загрузки»
 * остаются. Только при явном включении пользователь разрешает выгрузить
 * незашифрованный архив в Downloads при запуске движка, не чаще раза в 12 часов.
 */
object AutoBackup {

    private const val PREFS = "healdragg"
    private const val KEY_LAST = "auto_export_at"
    const val KEY_ENABLED = "auto_export_enabled"
    private val exporting = AtomicBoolean(false)
    private const val FOLDER = "Healdragg"
    private const val PREFIX = "healdragg-backup-"
    private const val MIN_INTERVAL = 12 * 60 * 60 * 1000L

    @SuppressLint("NewApi")
    fun maybeExport(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (!prefs.getBoolean(KEY_ENABLED, false)) return
        val last = prefs.getLong(KEY_LAST, 0L)
        if (System.currentTimeMillis() - last < MIN_INTERVAL || !exporting.compareAndSet(false, true)) return
        Thread({
            try { export(context.applicationContext) } finally { exporting.set(false) }
        }, "healdragg-autobackup").start()
    }

    @SuppressLint("NewApi")
    private fun export(context: Context) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val token = prefs.getString("api_token", "").orEmpty()
        val name = PREFIX + SimpleDateFormat("yyyyMMdd-HHmm", Locale.US).format(Date()) + ".zip"
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, name)
            put(MediaStore.Downloads.MIME_TYPE, "application/zip")
            put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/" + FOLDER)
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val resolver = context.contentResolver
        val uri = runCatching {
            resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
        }.getOrNull() ?: return
        val ok = runCatching {
            val conn = (URL("http://127.0.0.1:${EngineService.PORT}/api/backup/download?fresh=1")
                .openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = 5_000
                readTimeout = 180_000
                if (token.isNotBlank()) setRequestProperty("X-Api-Token", token)
            }
            try {
                check(conn.responseCode in 200..299)
                val output = resolver.openOutputStream(uri) ?: error("Не удалось открыть файл копии")
                var total = 0L
                conn.inputStream.use { source ->
                    output.use { target ->
                        val buffer = ByteArray(64 * 1024)
                        while (true) {
                            val count = source.read(buffer)
                            if (count < 0) break
                            check(prefs.getBoolean(KEY_ENABLED, false))
                            total += count
                            check(total <= 400L * 1024 * 1024)
                            target.write(buffer, 0, count)
                        }
                    }
                }
                check(total >= 512)
                val finished = ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }
                check(resolver.update(uri, finished, null, null) == 1)
            } finally {
                conn.disconnect()
            }
            true
        }.getOrDefault(false)
        if (!ok) runCatching { resolver.delete(uri, null, null) }
        if (ok) {
            prefs.edit().putLong(KEY_LAST, System.currentTimeMillis()).apply()
            prune(context, keep = 4)
        }
    }

    /** Оставляет несколько последних копий, чтобы «Загрузки» не пухли. */
    @SuppressLint("NewApi")
    private fun prune(context: Context, keep: Int) {
        val collection = MediaStore.Downloads.EXTERNAL_CONTENT_URI
        val projection = arrayOf(
            MediaStore.Downloads._ID,
            MediaStore.Downloads.DISPLAY_NAME,
            MediaStore.Downloads.DATE_ADDED,
        )
        val selection = "${MediaStore.Downloads.RELATIVE_PATH} = ?"
        val args = arrayOf("${Environment.DIRECTORY_DOWNLOADS}/$FOLDER/")
        val found = ArrayList<Pair<Uri, Long>>()
        runCatching {
            context.contentResolver.query(collection, projection, selection, args, null)?.use { cursor ->
                while (cursor.moveToNext()) {
                    val id = cursor.getLong(0)
                    val name = cursor.getString(1) ?: continue
                    if (!name.startsWith(PREFIX)) continue
                    found.add(Uri.withAppendedPath(collection, id.toString()) to cursor.getLong(2))
                }
            }
        }
        found.sortedByDescending { it.second }.drop(keep).forEach { (uri, _) ->
            runCatching { context.contentResolver.delete(uri, null, null) }
        }
    }

    /**
     * Самая свежая копия в «Загрузках» — путь восстановления после переустановки.
     * Возвращает uri и человекочитаемое имя.
     */
    @SuppressLint("NewApi")
    fun findLatest(context: Context): Pair<Uri, String>? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return null
        val collection = MediaStore.Downloads.EXTERNAL_CONTENT_URI
        val projection = arrayOf(
            MediaStore.Downloads._ID,
            MediaStore.Downloads.DISPLAY_NAME,
            MediaStore.Downloads.DATE_ADDED,
        )
        val selection = "${MediaStore.Downloads.DISPLAY_NAME} LIKE ?"
        val args = arrayOf("$PREFIX%")
        var best: Pair<Uri, String>? = null
        var bestDate = 0L
        runCatching {
            context.contentResolver.query(collection, projection, selection, args, null)?.use { cursor ->
                while (cursor.moveToNext()) {
                    val id = cursor.getLong(0)
                    val name = cursor.getString(1) ?: continue
                    val date = cursor.getLong(2)
                    if (date >= bestDate) {
                        bestDate = date
                        best = Uri.withAppendedPath(collection, id.toString()) to name
                    }
                }
            }
        }
        return best
    }

    /** Есть ли вообще папка с копиями (для подсказок в настройках). */
    @SuppressLint("NewApi")
    fun folderExists(context: Context): Boolean =
        File(
            Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS),
            FOLDER,
        ).isDirectory
}
