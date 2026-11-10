package net.syslikoff.healdragg.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import kotlin.math.abs

private val RU = Locale("ru", "RU")

fun relativeTime(ts: Long): String {
    if (ts <= 0L) return "—"
    val seconds = (System.currentTimeMillis() / 1000) - ts
    return when {
        seconds < 60 -> "только что"
        seconds < 3_600 -> "${seconds / 60} мин назад"
        seconds < 86_400 -> "${seconds / 3_600} ч назад"
        seconds < 172_800 -> "вчера"
        seconds < 604_800 -> "${seconds / 86_400} дн назад"
        else -> shortDate(ts)
    }
}

fun shortDate(ts: Long): String =
    if (ts <= 0L) "—" else SimpleDateFormat("d MMM", RU).format(Date(ts * 1000))

fun fullDate(ts: Long): String =
    if (ts <= 0L) "—" else SimpleDateFormat("d MMM yyyy, HH:mm", RU).format(Date(ts * 1000))

fun timeOnly(ts: Long): String =
    if (ts <= 0L) "" else SimpleDateFormat("HH:mm", RU).format(Date(ts * 1000))

/** «Сегодня», «Вчера» или дата — заголовки групп в ленте и алертах. */
fun dayLabel(ts: Long): String {
    if (ts <= 0L) return "Без даты"
    val now = Calendar.getInstance()
    val then = Calendar.getInstance().apply { timeInMillis = ts * 1000 }
    val sameYear = now.get(Calendar.YEAR) == then.get(Calendar.YEAR)
    val days = dayDiff(now, then)
    return when {
        days == 0L -> "Сегодня"
        days == 1L -> "Вчера"
        days in 2..6 -> "${days} дн назад"
        sameYear -> SimpleDateFormat("d MMMM", RU).format(then.time)
        else -> SimpleDateFormat("d MMMM yyyy", RU).format(then.time)
    }
}

private fun dayDiff(now: Calendar, then: Calendar): Long {
    val a = now.clone() as Calendar
    val b = then.clone() as Calendar
    a.set(Calendar.HOUR_OF_DAY, 0)
    a.set(Calendar.MINUTE, 0)
    a.set(Calendar.SECOND, 0)
    a.set(Calendar.MILLISECOND, 0)
    b.set(Calendar.HOUR_OF_DAY, 0)
    b.set(Calendar.MINUTE, 0)
    b.set(Calendar.SECOND, 0)
    b.set(Calendar.MILLISECOND, 0)
    return abs((a.timeInMillis - b.timeInMillis) / 86_400_000L)
}

fun compactNumber(value: Int): String = when {
    value < 1_000 -> value.toString()
    value < 10_000 -> String.format(RU, "%.1fк", value / 1_000f)
    value < 1_000_000 -> "${value / 1_000}к"
    else -> String.format(RU, "%.1f млн", value / 1_000_000f)
}

fun membersLabel(count: Int): String = when {
    count <= 0 -> ""
    count < 1_000 -> "$count участн."
    else -> "${compactNumber(count)} участн."
}

fun copyText(context: Context, label: String, text: String) {
    val manager = context.getSystemService(ClipboardManager::class.java) ?: return
    manager.setPrimaryClip(ClipData.newPlainText(label, text))
}

fun openLink(context: Context, url: String?): Boolean {
    if (url.isNullOrBlank()) return false
    return try {
        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        true
    } catch (_: Exception) {
        false
    }
}

fun telegramLink(username: String?): String? =
    username?.takeIf { it.isNotBlank() }?.let { "https://t.me/$it" }

/** Флаг страны по ISO-коду. Для обобщённых регионов (арабский мир) — глобус. */
fun countryFlag(code: String?): String {
    if (code.isNullOrBlank() || code.length != 2) return "🌍"
    if (code.uppercase() == "AR") return "🌍"
    val upper = code.uppercase()
    val first = if (upper[0] in 'A'..'Z') upper[0].code - 'A'.code + 0x1F1E6 else return "🌍"
    val second = if (upper[1] in 'A'..'Z') upper[1].code - 'A'.code + 0x1F1E6 else return "🌍"
    return String(Character.toChars(first)) + String(Character.toChars(second))
}

/** Человеческая подпись пола из ответа движка. */
fun genderLabel(value: String?): String? = when (value) {
    "male" -> "мужской"
    "female" -> "женский"
    else -> null
}

/** Человеческая подпись вложения из базы (gif, photo, video…). */
fun mediaLabel(media: String?): String? = when (media?.lowercase()) {
    null, "" -> null
    "gif" -> "гиф"
    "photo" -> "фото"
    "video" -> "видео"
    "audio" -> "аудио"
    "sticker" -> "стикер"
    "document" -> "файл"
    "webpage" -> "ссылка"
    "geo", "geolive", "venue" -> "геометка"
    "poll" -> "опрос"
    "contact" -> "контакт"
    else -> media
}
