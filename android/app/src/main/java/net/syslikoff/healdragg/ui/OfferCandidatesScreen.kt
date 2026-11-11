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
fun OfferCandidatesScreen(vm: AppViewModel, onBack: () -> Unit, onOpenProfile: (String) -> Unit,
    onOpenAccounts: () -> Unit, initialQuery: String = "") {
    val api = vm.api
    val scope = rememberCoroutineScope()
    val clipboard = LocalClipboardManager.current
    var query by rememberSaveable(initialQuery) { mutableStateOf(initialQuery) }
    var exactSlug by rememberSaveable(initialQuery) { mutableStateOf(initialQuery.isNotBlank()) }
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
    var currency by rememberSaveable { mutableStateOf("star") }
    var usdRate by rememberSaveable { mutableStateOf("") }
    var markup by rememberSaveable { mutableStateOf("0") }
    var perOffer by rememberSaveable { mutableStateOf("") }
    var budget by rememberSaveable { mutableStateOf("") }
    var maxOffers by rememberSaveable { mutableStateOf("5") }
    var planJson by rememberSaveable { mutableStateOf<String?>(null) }
    val plan = remember(planJson) { planJson?.let { JSONObject(it) } }
    var planBusy by remember { mutableStateOf(false) }

    fun filters(): JSONObject {
        val body = JSONObject().put("query", query.trim()).put("location", location)
            .put("gender", gender).put("country", country).put("backdrop", backdrop.trim()).put("limit", 40)
        if (exactSlug && query.trim() == initialQuery) body.put("slug_exact", initialQuery)
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

    fun preview() {
        if (planBusy) return
        val client = api ?: run { error = "Движок не запущен"; return }
        val body = try {
            val currentFilters = filters().apply { remove("limit") }
            val per = perOffer.toIntOrNull() ?: throw IllegalArgumentException("Укажите максимум на NFT")
            val total = budget.toIntOrNull() ?: throw IllegalArgumentException("Укажите общий бюджет")
            val count = maxOffers.toIntOrNull() ?: throw IllegalArgumentException("Укажите количество")
            JSONObject().put("filters", currentFilters).put("currency", currency)
                .put("usd_per_unit", usdRate.trim().replace(',', '.'))
                .put("markup_percent", markup.trim().replace(',', '.'))
                .put("max_per_offer", per).put("total_budget", total).put("max_offers", count)
                .put("one_per_owner", true)
        } catch (e: IllegalArgumentException) { error = e.message; return }
        planBusy = true
        planJson = null
        error = null
        scope.launch {
            try { planJson = client.offerPreview(body).toString() }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { error = e.message ?: "Не удалось рассчитать план" }
            finally { planBusy = false }
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
                Text("Только локальный предварительный отбор. Выбор ничего не отправляет и не создаёт оффер. Автоматическая отправка предложений пока не реализована.", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
                TextButton(onClick = onOpenAccounts) { Text("Бизнес-бот @echidbot: проверить или подключить") }
                Text("Откройте карточку нужного аккаунта и проверьте бота. Подключение требует отдельного согласия и само по себе не отправляет предложения.", color = Hd.Muted, style = MaterialTheme.typography.labelSmall)
                Spacer(Modifier.height(8.dp))
                HdTextField(query, { query = it; exactSlug = false }, "Владелец или NFT", hint = "Имя, @ник, ID или название")
                if (exactSlug) Text("Только выбранный NFT. Измените поиск, чтобы расширить выборку.", color = Hd.Accent, style = MaterialTheme.typography.labelSmall)
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
        item("plan-settings") {
            HdCard {
                Text("Автоподбор партии · черновик", style = MaterialTheme.typography.titleMedium, color = Hd.Text)
                Text("Подбирает по фильтрам выше, не более одного NFT на владельца. Это не отправка и не актуальная цена.", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = currency == "star", onClick = { currency = "star" }, label = { Text("star") })
                    FilterChip(selected = currency == "gram", onClick = { currency = "gram" }, label = { Text("gram") })
                }
                HdTextField(usdRate, { usdRate = it }, "USD за 1 $currency", hint = "Укажите курс вручную", keyboard = KeyboardType.Decimal)
                HdTextField(markup, { markup = it }, "Добавить к оценке, %", keyboard = KeyboardType.Decimal)
                HdTextField(perOffer, { perOffer = it }, "Максимум за один NFT, $currency", keyboard = KeyboardType.Number)
                HdTextField(budget, { budget = it }, "Общий лимит партии, $currency", keyboard = KeyboardType.Number)
                HdTextField(maxOffers, { maxOffers = it }, "Максимум предложений", keyboard = KeyboardType.Number)
                Text("Цена округляется вверх до целых единиц. Записи без оценки или username пропускаются. Курс и оценка могут устареть.", color = Hd.Warn, style = MaterialTheme.typography.bodySmall)
                Button(onClick = { preview() }, enabled = !planBusy, modifier = Modifier.fillMaxWidth()) {
                    Text(if (planBusy) "Подбираю…" else "Рассчитать черновик")
                }
            }
        }
        error?.let { message -> item("error") { Text(message, color = Hd.Bad) } }
        plan?.let { draft ->
            val draftRows = draft.arr("rows")
            item("plan-summary") {
                HdCard {
                    Text("Черновик: ${draftRows.length()} · сумма ${draft.optLong("total_amount")} ${draft.optString("currency")}", color = Hd.Text, style = MaterialTheme.typography.titleMedium)
                    Text("${draft.optString("notice")} Перед ручной отправкой в @echidbot проверьте каждый NFT, продавца, сумму и курс. Черновик отражает параметры на момент расчёта.", color = Hd.Warn, style = MaterialTheme.typography.bodySmall)
                    Text("Проверено записей: ${draft.optInt("examined")}" + if (draft.optBoolean("truncated")) " · выборка ограничена" else "", color = Hd.Muted, style = MaterialTheme.typography.labelSmall)
                    val skipped = draft.optJSONObject("skipped")
                    if (skipped != null) Text("Пропущено: без цены ${skipped.optInt("no_price")}, без username ${skipped.optInt("no_username")}, повтор владельца ${skipped.optInt("same_owner")}, выше лимита ${skipped.optInt("over_per_offer")}, не вошло в бюджет ${skipped.optInt("over_budget")}, некорректная ссылка ${skipped.optInt("invalid_slug")}", color = Hd.Muted, style = MaterialTheme.typography.labelSmall)
                }
            }
            items((0 until draftRows.length()).mapNotNull { draftRows.optJSONObject(it) }, key = { "plan-${it.optString("slug")}" }) { row ->
                HdCard {
                    Text("${row.optString("slug")} · @${row.optString("owner_username")}", color = Hd.Text, style = MaterialTheme.typography.titleSmall)
                    Text("Черновик: ${row.optLong("amount")} ${row.optString("currency")} · старая оценка $${BigDecimal.valueOf(row.optLong("value_usd_cents"), 2).toPlainString()}", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
                    TextButton(onClick = { clipboard.setText(AnnotatedString(row.optString("command"))) }) { Text("Скопировать команду для проверки") }
                }
            }
        }
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
