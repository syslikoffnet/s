package net.syslikoff.healdragg.ui

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.math.BigDecimal

/** Только локальный предварительный просмотр. Отправка предложений не реализована. */
@Composable
fun OfferCandidatesScreen(vm: AppViewModel, onBack: () -> Unit, onOpenProfile: (String) -> Unit) {
    val api = vm.api
    val scope = rememberCoroutineScope()
    val clipboard = LocalClipboardManager.current
    var query by rememberSaveable { mutableStateOf("") }
    var maxCount by rememberSaveable { mutableStateOf("") }
    var minimum by rememberSaveable { mutableStateOf("") }
    var backdrop by rememberSaveable { mutableStateOf("") }
    var location by rememberSaveable { mutableStateOf("any") }
    var gender by rememberSaveable { mutableStateOf("any") }
    var country by rememberSaveable { mutableStateOf("any") }
    var advancedOpen by rememberSaveable { mutableStateOf(false) }
    var selected by rememberSaveable { mutableStateOf<String?>(null) }
    var rows by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    var next by remember { mutableStateOf<String?>(null) }
    var applied by remember { mutableStateOf<JSONObject?>(null) }
    var busy by remember { mutableStateOf(false) }
    var loaded by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    fun filters(): JSONObject {
        val body = JSONObject().put("query", query.trim()).put("location", location)
            .put("gender", gender).put("country", country).put("backdrop", backdrop.trim()).put("limit", 40)
        if (maxCount.isNotBlank()) {
            val count = maxCount.trim().toIntOrNull()
            require(count != null && count in 0..1_000_000) { "Введите целое число NFT от 0 до 1000000" }
            body.put("max_nft", count)
        }
        if (minimum.isNotBlank()) {
            val text = minimum.trim().replace(',', '.')
            require(text.matches(Regex("[0-9]{1,10}(\\.[0-9]{1,2})?"))) { "Введите оценку в долларах, до двух знаков после запятой" }
            val cents = BigDecimal(text).movePointRight(2).longValueExact()
            require(cents <= 1_000_000_000_000L) { "Слишком большая оценка" }
            body.put("min_value_usd_cents", cents)
        }
        return body
    }

    fun load(reset: Boolean) {
        if (busy) return
        val client = api ?: run { error = "Движок не запущен"; return }
        val body = try {
            if (reset) filters() else JSONObject(applied?.toString() ?: return).put("after", next ?: return)
        } catch (e: IllegalArgumentException) {
            error = e.message
            return
        }
        busy = true
        error = null
        scope.launch {
            try {
                val data = client.offerCandidates(body)
                val arr = data.arr("rows")
                val page = (0 until arr.length()).mapNotNull { arr.optJSONObject(it) }
                rows = if (reset) page else rows + page
                next = data.str("next_after")
                if (reset) {
                    applied = JSONObject(body.toString())
                    selected = null
                }
                loaded = true
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                error = e.message ?: "Не удалось прочитать кандидатов"
            } finally {
                busy = false
            }
        }
    }

    LaunchedEffect(api) { if (api != null && !loaded) load(true) }
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 100.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item("filters") {
            HdCard {
                OutlinedButton(onClick = onBack) { Text("Назад к подаркам") }
                Text("Подбор кандидатов", style = MaterialTheme.typography.titleMedium, color = Hd.Text)
                Spacer(Modifier.height(6.dp))
                Text("Только локальный предварительный отбор. Выбор ничего не отправляет и не создаёт оффер.", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
                Spacer(Modifier.height(8.dp))
                HdTextField(query, { query = it }, "Владелец или NFT", hint = "Имя, @ник, ID или название")
                HdTextField(maxCount, { maxCount = it }, "Не больше NFT в базе", hint = "Например, 1 или 2", keyboard = KeyboardType.Number)
                TextButton(onClick = { advancedOpen = !advancedOpen }) {
                    Text(if (advancedOpen) "Скрыть дополнительные фильтры" else "Дополнительные фильтры ▾")
                }
                if (!advancedOpen && (minimum.isNotBlank() || backdrop.isNotBlank() || location != "any" || gender != "any" || country != "any")) {
                    Text("Дополнительные фильтры заданы", color = Hd.Accent, style = MaterialTheme.typography.labelSmall)
                }
                if (advancedOpen) {
                HdTextField(minimum, { minimum = it }, "Оценка от, USD", hint = "Например, 25.50", keyboard = KeyboardType.Decimal)
                HdTextField(backdrop, { backdrop = it }, "Название фона", hint = "Например, Black")
                Text("Местоположение по наблюдению", color = Hd.Muted, style = MaterialTheme.typography.labelMedium)
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf("any" to "Любое", "telegram" to "Без адреса", "wallet" to "Адрес чейна").forEach { (key, label) ->
                        FilterChip(selected = location == key, onClick = { location = key }, label = { Text(label) })
                    }
                }
                Text("Предположение по имени", color = Hd.Muted, style = MaterialTheme.typography.labelMedium)
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    FilterChip(selected = gender == "female", onClick = { gender = if (gender == "female") "any" else "female" }, label = { Text("Женский") })
                    FilterChip(selected = country == "RU", onClick = { country = if (country == "RU") "any" else "RU" }, label = { Text("RU") })
                }
                }
                Button(onClick = { load(true) }, enabled = !busy, modifier = Modifier.fillMaxWidth()) { Text(if (busy) "Загрузка…" else "Применить фильтры") }
                Text("Число NFT — только найденное в базе, не полный инвентарь. Пол и страна — предположения по имени. Адрес не доказывает владельца, отсутствие адреса не доказывает владение в Telegram. Оценка и владелец могут устареть.", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
            }
        }
        error?.let { message -> item("error") { Text(message, color = Hd.Bad) } }
        if (loaded && rows.isEmpty()) item("empty") { Text("Подходящих наблюдений нет", color = Hd.Muted) }
        items(rows, key = { it.optString("slug") }) { row ->
            val slug = row.optString("slug")
            val ownerId = row.optLong("owner_tg_id")
            val handle = row.str("owner_username")?.let { "@$it" } ?: row.str("owner_name") ?: "ID $ownerId"
            HdCard {
                Text("${row.str("title") ?: "NFT"} · $slug", style = MaterialTheme.typography.titleSmall, color = Hd.Text)
                Text("$handle · ID $ownerId · найдено NFT: ${row.optInt("observed_nft_count")}", color = Hd.Text)
                val place = if (row.optInt("on_chain") == 1) "есть адрес чейна" else "адрес неизвестен"
                val value = if (row.optLong("value_usd") > 0) "оценка $${BigDecimal.valueOf(row.optLong("value_usd"), 2).toPlainString()}" else "оценки нет"
                val bg = row.str("backdrop_name") ?: "неизвестен"
                Text("$place · $value · фон: $bg", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
                Text("Наблюдение: ${fullDate(row.optLong("seen_at"))}", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { selected = if (selected == slug) null else slug }) { Text(if (selected == slug) "Выбрано" else "Выбрать") }
                    OutlinedButton(onClick = { onOpenProfile(ownerId.toString()) }) { Text("Профиль") }
                }
                if (selected == slug) {
                    Text("Ссылка: https://t.me/nft/$slug", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
                    OutlinedButton(onClick = { clipboard.setText(AnnotatedString("https://t.me/nft/$slug")) }) { Text("Скопировать ссылку") }
                }
            }
        }
        if (next != null) item("more") {
            OutlinedButton(onClick = { load(false) }, enabled = !busy, modifier = Modifier.fillMaxWidth()) { Text("Показать ещё") }
            Text("Следующая страница использует последние применённые фильтры.", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
        }
    }
}
