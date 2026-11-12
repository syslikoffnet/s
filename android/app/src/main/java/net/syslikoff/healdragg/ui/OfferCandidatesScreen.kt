package net.syslikoff.healdragg.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
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
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.math.BigDecimal

/** Подбор, онлайн-проверка и отдельно подтверждённая отправка команд. */
@Composable
fun OfferCandidatesScreen(vm: AppViewModel, onBack: () -> Unit, onOpenProfile: (String) -> Unit,
    onOpenAccounts: () -> Unit, initialQuery: String = "") {
    val api = vm.api
    val scope = rememberCoroutineScope()
    val clipboard = LocalClipboardManager.current
    var section by rememberSaveable { mutableStateOf("home") }
    var sessionSearch by rememberSaveable { mutableStateOf("") }
    BackHandler(enabled = section != "home") { section = "home" }
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
    var planInputJson by rememberSaveable { mutableStateOf<String?>(null) }
    var batch by remember { mutableStateOf<JSONObject?>(null) }
    var batchBusy by remember { mutableStateOf(false) }
    var language by rememberSaveable { mutableStateOf("ru") }
    var delivery by remember { mutableStateOf<JSONObject?>(null) }
    var deliveryBusy by remember { mutableStateOf(false) }
    var deliveryConfirm by remember { mutableStateOf<JSONObject?>(null) }
    var reconcileDialog by remember { mutableStateOf(false) }
    val plan = remember(planJson) { planJson?.let { JSONObject(it) } }
    var planBusy by remember { mutableStateOf(false) }
    var offerSession by rememberSaveable { mutableStateOf("") }
    var verifying by remember { mutableStateOf(false) }
    var verified by remember { mutableStateOf<JSONObject?>(null) }
    val offerSessions = vm.sessions.filter { it.active && it.purpose == "offers" }

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
        planInputJson = null
        error = null
        scope.launch {
            try { planJson = client.offerPreview(body).toString(); planInputJson = body.toString() }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { error = e.message ?: "Не удалось рассчитать план" }
            finally { planBusy = false }
        }
    }

    fun verify(row: JSONObject) {
        if (verifying) return
        val client = api ?: run { error = "Движок не запущен"; return }
        if (offerSessions.none { it.name == offerSession }) {
            error = "Выберите активный аккаунт предложений"
            return
        }
        val body = JSONObject().put("session", offerSession).put("slug", row.optString("slug"))
            .put("seller_id", row.optLong("owner_tg_id"))
        verifying = true
        error = null
        scope.launch {
            try { verified = client.verifyOffer(body) }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { error = e.message ?: "Не удалось получить данные Telegram" }
            finally { verifying = false }
        }
    }

    fun startBatch() {
        val client = api ?: run { error = "Движок не запущен"; return }
        val input = planInputJson ?: run { error = "Сначала рассчитайте черновик"; return }
        if (offerSessions.none { it.name == offerSession }) { error = "Выберите аккаунт предложений"; return }
        batchBusy = true
        error = null
        scope.launch {
            try { batch = client.startOfferBatch(offerSession, JSONObject(input)).optJSONObject("run") }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { error = e.message ?: "Не удалось запустить проверку партии" }
            finally { batchBusy = false }
        }
    }
    fun stopBatch() {
        val client = api ?: return
        batchBusy = true
        scope.launch {
            try { batch = client.stopOfferBatch().optJSONObject("run") }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { error = e.message ?: "Не удалось остановить проверку" }
            finally { batchBusy = false }
        }
    }

    fun refreshDelivery() {
        val client = api ?: return
        scope.launch {
            try { delivery = client.deliveryStatus() }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { error = e.message ?: "Не удалось прочитать журнал отправки" }
        }
    }
    fun prepareDelivery() {
        val client = api ?: run { error = "Движок не запущен"; return }
        val input = planInputJson ?: run { error = "Сначала рассчитайте черновик"; return }
        if (offerSessions.none { it.name == offerSession }) { error = "Выберите аккаунт предложений"; return }
        deliveryBusy = true
        error = null
        scope.launch {
            try { deliveryConfirm = client.prepareDelivery(offerSession, JSONObject(input), language).optJSONObject("run") }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { error = e.message ?: "Не удалось подготовить отправку" }
            finally { deliveryBusy = false; refreshDelivery() }
        }
    }
    fun stopDelivery() {
        val client = api ?: return
        deliveryBusy = true
        scope.launch {
            try { client.stopDelivery(); refreshDelivery() }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { error = e.message ?: "Не удалось остановить отправку" }
            finally { deliveryBusy = false }
        }
    }

    LaunchedEffect(api) { if (api != null && !loaded) load(true) }
    LaunchedEffect(api) {
        val client = api ?: return@LaunchedEffect
        while (true) {
            try { batch = client.offerBatch().optJSONObject("run") }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { /* Проверку можно обновить при следующем опросе. */ }
            try { delivery = client.deliveryStatus() }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { /* Повторим получение статуса. */ }
            delay(3000)
        }
    }
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 100.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item("section-nav") {
            HdCard {
                TextButton(onClick = { if (section == "home") onBack() else section = "home" }) { Text(if (section == "home") "← Назад" else "← Раздел предложений") }
                Text(when (section) { "search" -> "Поиск NFT"; "plan" -> "План и проверка"; "delivery" -> "Отправка и журнал"; else -> "Предложения" }, style = MaterialTheme.typography.headlineSmall, color = Hd.Text)
                if (section == "home") {
                    Text("Подбор, проверка и отправка — отдельные шаги. Команды отправляются только после подтверждения.", color = Hd.Muted)
                    Button(onClick = { section = "search" }, modifier = Modifier.fillMaxWidth()) { Text("Найти NFT") }
                    OutlinedButton(onClick = { section = "plan" }, modifier = Modifier.fillMaxWidth()) { Text("План и проверка партии") }
                    OutlinedButton(onClick = { section = "delivery" }, modifier = Modifier.fillMaxWidth()) { Text("Отправка и журнал") }
                    TextButton(onClick = onOpenAccounts) { Text("Аккаунты и бизнес-бот") }
                }
            }
        }
        if (section == "search") item("filters") {
            HdCard {
                Text("Подбор кандидатов", style = MaterialTheme.typography.titleMedium, color = Hd.Text)
                Spacer(Modifier.height(6.dp))
                Text("Подбор сам по себе ничего не отправляет. Отправка команд запускается отдельно после подтверждения списка и сумм.", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
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
        if (section == "search" || section == "plan" || section == "delivery") item("verify-account") {
            HdCard {
                Text("Проверка в Telegram", style = MaterialTheme.typography.titleMedium, color = Hd.Text)
                Text("Выберите аккаунт предложений и проверьте NFT в списке или черновике. Это только снимок: сообщения не отправляются.", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
                HdTextField(sessionSearch, { sessionSearch = it }, "Найти аккаунт", hint = "Имя или @username")
                Text(if (offerSession.isEmpty()) "Выберите аккаунт" else "Выбран: $offerSession", color = Hd.Muted, style = MaterialTheme.typography.labelSmall)
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    offerSessions.filter { it.name.contains(sessionSearch.trim(), true) || (it.username ?: "").contains(sessionSearch.trim().removePrefix("@"), true) }.take(20).forEach { session ->
                        FilterChip(selected = offerSession == session.name, onClick = { offerSession = session.name },
                            label = { Text(session.username?.let { "@$it" } ?: session.name) })
                    }
                }
                if (offerSessions.isEmpty()) Text("Нет активного аккаунта предложений. Добавьте его в аккаунтах.", color = Hd.Warn, style = MaterialTheme.typography.bodySmall)
            }
        }
        if (section == "plan") item("plan-settings") {
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
        if (section == "delivery") item("delivery") {
            HdCard {
                Text("Автоотправка команд владельцам", style = MaterialTheme.typography.titleMedium, color = Hd.Text)
                Text("Команды будут видны владельцам в их личных чатах. Каждая команда содержит NFT, цену, валюту и язык; перед отправкой проверяются владелец и подключённый бизнес-бот. Обработка ботом и создание оффера не подтверждаются.", color = Hd.Warn, style = MaterialTheme.typography.bodySmall)
                Text("Язык команды", color = Hd.Muted, style = MaterialTheme.typography.labelMedium)
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf("ru", "en", "zh", "ar", "ko", "ja", "tl", "th", "ms").forEach { lang ->
                        FilterChip(selected = language == lang, onClick = { language = lang }, label = { Text(lang) })
                    }
                }
                Text("Не чаще одного сообщения в минуту, не более 20 в сутки; суточный лимит суммы — 10000 star или 10 gram. Лимиты черновика тоже действуют; оценка и курс не гарантируют рыночную цену.", color = Hd.Muted, style = MaterialTheme.typography.labelSmall)
                val run = delivery?.optJSONObject("run")
                val running = run?.optString("status") == "running"
                val blocked = delivery?.optBoolean("restore_blocked") == true || (delivery?.optInt("unresolved") ?: 0) > 0
                if (blocked) {
                    Text("История могла устареть или результат отправки неизвестен. Проверьте чаты и предложения в Telegram: повтор может создать дубликат.", color = Hd.Warn, style = MaterialTheme.typography.bodySmall)
                    OutlinedButton(onClick = { reconcileDialog = true }, enabled = !deliveryBusy) { Text("Подтвердить ручную сверку") }
                }
                if (running) {
                    Button(onClick = { stopDelivery() }, enabled = !deliveryBusy, modifier = Modifier.fillMaxWidth()) { Text("Остановить отправку") }
                    Text("Уже отправленный запрос отменить нельзя. При неизвестном результате проверьте чат.", color = Hd.Warn, style = MaterialTheme.typography.labelSmall)
                } else {
                    Button(onClick = { prepareDelivery() }, enabled = !deliveryBusy && !blocked && planInputJson != null && delivery != null, modifier = Modifier.fillMaxWidth()) { Text("Подготовить отправку") }
                }
                if (run != null) {
                    Text("Партия: ${when (run.optString("status")) { "prepared" -> "подготовлена"; "running" -> "отправляется"; "done" -> "завершена"; "stopped" -> "остановлена"; "interrupted" -> "прервана"; else -> "ошибка" }}", color = Hd.Text, style = MaterialTheme.typography.bodySmall)
                    run.str("error")?.let { Text(it, color = Hd.Warn, style = MaterialTheme.typography.bodySmall) }
                }
                TextButton(onClick = { refreshDelivery() }) { Text("Обновить журнал") }
                val history = delivery?.arr("history")
                if (history != null) for (i in 0 until history.length()) {
                    val attempt = history.optJSONObject(i) ?: continue
                    Text("${attempt.optString("slug")} · ID ${attempt.optLong("seller_id")} · ${attempt.optLong("amount")} ${attempt.optString("currency")} · ${if (attempt.optString("status") == "telegram_ack") "принято Telegram" else "исход неизвестен"}", color = Hd.Text, style = MaterialTheme.typography.labelSmall)
                    Text("${fullDate(attempt.optLong("created_at"))} · аккаунт ID ${attempt.optLong("buyer_id")}\n${attempt.optString("command")}", color = Hd.Muted, style = MaterialTheme.typography.labelSmall)
                }
            }
        }
        if (section == "plan") item("batch-check") {
            HdCard {
                Text("Проверка партии в Telegram", style = MaterialTheme.typography.titleMedium, color = Hd.Text)
                Text("По параметрам последнего черновика заново подбираются NFT; список мог измениться. Проверка обновляет оценки по Telegram и применяет введённый вручную курс и лимиты. Это не отправка, не резервирование средств и не запуск офферов.", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
                val running = batch?.optString("status") == "running"
                if (running) {
                    OutlinedButton(onClick = { stopBatch() }, enabled = !batchBusy, modifier = Modifier.fillMaxWidth()) { Text("Остановить проверку") }
                } else {
                    OutlinedButton(onClick = { startBatch() }, enabled = !batchBusy && planInputJson != null, modifier = Modifier.fillMaxWidth()) { Text("Проверить партию") }
                }
                batch?.let { run ->
                    val batchRows = run.arr("rows")
                    val done = (0 until batchRows.length()).count { batchRows.optJSONObject(it)?.optString("status") in listOf("checked", "skipped") }
                    Text("Статус: ${when (run.optString("status")) { "running" -> "проверяется"; "done" -> "завершена"; "stopped" -> "остановлена"; "interrupted" -> "прервана при перезапуске"; else -> "ошибка" }} · $done/${batchRows.length()}", color = Hd.Text, style = MaterialTheme.typography.bodySmall)
                    run.str("error")?.let { Text(it, color = Hd.Warn, style = MaterialTheme.typography.bodySmall) }
                    Text("После перезапуска проверка сама не возобновляется. Снимки устаревают; бот и создание оффера не подтверждены.", color = Hd.Warn, style = MaterialTheme.typography.labelSmall)
                    for (index in 0 until batchRows.length()) {
                        val row = batchRows.optJSONObject(index) ?: continue
                        val result = row.optJSONObject("result")
                        val stateText = when (row.optString("status")) {
                            "checked" -> "проверен · ${result?.optLong("amount")} ${result?.optString("currency")}"
                            "skipped" -> "пропущен · ${result?.optJSONArray("reasons")?.let { reasons -> (0 until reasons.length()).joinToString(", ") { reasons.optString(it) } } ?: "проверьте данные"}"
                            "checking" -> "проверяется"
                            else -> "ожидает"
                        }
                        Text("${row.optString("slug")} · $stateText", color = if (row.optString("status") == "skipped") Hd.Warn else Hd.Text, style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
        }
        error?.let { message -> item("error") { Text(message, color = Hd.Bad) } }
        if (section == "plan") plan?.let { draft ->
            val draftRows = draft.arr("rows")
            item("plan-summary") {
                HdCard {
                    Text("Черновик: ${draftRows.length()} · сумма ${draft.optLong("total_amount")} ${draft.optString("currency")}", color = Hd.Text, style = MaterialTheme.typography.titleMedium)
                    Text("${draft.optString("notice")} Команда предназначена для личного чата владельца NFT, а не чата бота. Перед ручной отправкой проверьте владельца по ID, NFT, сумму и курс. Черновик отражает параметры на момент расчёта; обработка бизнес-ботом не гарантирована.", color = Hd.Warn, style = MaterialTheme.typography.bodySmall)
                    Text("Проверено записей: ${draft.optInt("examined")}" + if (draft.optBoolean("truncated")) " · выборка ограничена" else "", color = Hd.Muted, style = MaterialTheme.typography.labelSmall)
                    val skipped = draft.optJSONObject("skipped")
                    if (skipped != null) Text("Пропущено: без цены ${skipped.optInt("no_price")}, без username ${skipped.optInt("no_username")}, повтор владельца ${skipped.optInt("same_owner")}, выше лимита ${skipped.optInt("over_per_offer")}, не вошло в бюджет ${skipped.optInt("over_budget")}, некорректная ссылка ${skipped.optInt("invalid_slug")}", color = Hd.Muted, style = MaterialTheme.typography.labelSmall)
                }
            }
            items((0 until draftRows.length()).mapNotNull { draftRows.optJSONObject(it) }, key = { "plan-${it.optString("slug")}" }) { row ->
                HdCard {
                    Text("${row.optString("slug")} · @${row.optString("owner_username")}", color = Hd.Text, style = MaterialTheme.typography.titleSmall)
                    Text("Черновик: ${row.optLong("amount")} ${row.optString("currency")} · старая оценка $${BigDecimal.valueOf(row.optLong("value_usd_cents"), 2).toPlainString()}", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
                    TextButton(onClick = { clipboard.setText(AnnotatedString(row.optString("command"))) }) { Text("Скопировать команду для чата владельца") }
                    OutlinedButton(onClick = { verify(row) }, enabled = !verifying) {
                        Text(if (verifying) "Проверяю…" else "Проверить в Telegram")
                    }
                }
            }
        }
        if (section == "search" && loaded && rows.isEmpty()) item("empty") { Text("Подходящих наблюдений нет", color = Hd.Muted) }
        if (section == "search") items(rows, key = { it.optString("slug") }) { row ->
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
                    OutlinedButton(onClick = { verify(row) }, enabled = !verifying) {
                        Text(if (verifying) "Проверяю…" else "Проверить в Telegram")
                    }
                    Text("Ссылка: https://t.me/nft/$slug", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
                    OutlinedButton(onClick = { clipboard.setText(AnnotatedString("https://t.me/nft/$slug")) }) { Text("Скопировать ссылку") }
                }
            }
        }
        if (section == "search" && next != null) item("more") {
            OutlinedButton(onClick = { load(false) }, enabled = !busy, modifier = Modifier.fillMaxWidth()) { Text("Показать ещё") }
            Text("Следующая страница использует последние применённые фильтры.", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
        }
    }
    deliveryConfirm?.let { run ->
        val sendRows = run.arr("rows")
        AlertDialog(
            onDismissRequest = { if (!deliveryBusy) deliveryConfirm = null },
            title = { Text("Подтвердить отправку") },
            text = {
                androidx.compose.foundation.layout.Column(Modifier.verticalScroll(rememberScrollState())) {
                    Text("Аккаунт: ${run.optString("session")}\nКоманд: ${sendRows.length()} · сумма ${run.optLong("total")} ${run.optString("currency")}\n\nКоманды будут отправлены в личные чаты этих владельцев, не боту. Оффер и оплата не подтверждаются. Отмена не отзовёт уже отправленное сообщение.\n")
                    for (i in 0 until sendRows.length()) {
                        val item = sendRows.optJSONObject(i) ?: continue
                        Text("ID ${item.optLong("seller_id")}: ${item.optString("command")}\n", style = MaterialTheme.typography.bodySmall)
                    }
                }
            },
            confirmButton = { TextButton(enabled = !deliveryBusy, onClick = {
                val client = api ?: return@TextButton
                deliveryBusy = true
                scope.launch {
                    try { client.startDelivery(run.optString("id")); deliveryConfirm = null; refreshDelivery() }
                    catch (e: CancellationException) { throw e }
                    catch (e: Exception) { error = e.message ?: "Не удалось запустить отправку"; deliveryConfirm = null; refreshDelivery() }
                    finally { deliveryBusy = false }
                }
            }) { Text(if (deliveryBusy) "Запускаю…" else "Запустить отправку") } },
            dismissButton = { TextButton(enabled = !deliveryBusy, onClick = { deliveryConfirm = null }) { Text("Отмена") } },
        )
    }
    if (reconcileDialog) {
        AlertDialog(
            onDismissRequest = { reconcileDialog = false },
            title = { Text("Сверка истории") },
            text = { Text("Убедитесь в Telegram, что проверили все неопределённые попытки и возможные отправки после восстановления копии. Снятие блокировки не удаляет журнал и не разрешает повтор по тому же NFT.") },
            confirmButton = { TextButton(onClick = {
                reconcileDialog = false
                scope.launch {
                    try { api?.acknowledgeDelivery(); refreshDelivery() }
                    catch (e: CancellationException) { throw e }
                    catch (e: Exception) { error = e.message ?: "Не удалось подтвердить сверку" }
                }
            }) { Text("Проверил") } },
            dismissButton = { TextButton(onClick = { reconcileDialog = false }) { Text("Отмена") } },
        )
    }
    verified?.let { result ->
        val bot = result.optJSONObject("bot")
        val botOk = bot != null && bot.optString("status") == "connected" && bot.optBoolean("all_chats") && bot.arr("missing_rights").length() == 0
        val value = if (result.isNull("value_usd_cents")) "нет оценки" else "$" + BigDecimal.valueOf(result.optLong("value_usd_cents"), 2).toPlainString()
        AlertDialog(
            onDismissRequest = { verified = null },
            title = { Text("Снимок из Telegram") },
            text = { Text("${result.optString("slug")} · @${result.optString("seller_username")} · ID ${result.optLong("seller_id")}\n\nВладелец NFT: ${if (result.optBoolean("owner_matches")) "совпадает" else if (result.optString("owner_state") == "different") "изменился" else "не удалось подтвердить"}\nUsername: ${if (result.optBoolean("username_matches")) "совпадает" else "не подтверждён или изменился"}\nОценка Telegram: $value\nАккаунт ID ${result.optLong("account_id")} · Premium: ${if (result.optBoolean("premium")) "да" else "не подтверждён"}\nБизнес-бот: ${if (botOk) "подключён, проверенные права и доступ к чатам есть" else "проверьте настройки в аккаунтах"}\nПроверено: ${fullDate(result.optLong("checked_at"))}\n\n${result.optString("notice")} Курс и расчёт черновика здесь не проверяются; оценка Telegram не обязательно рыночная. Данные могут измениться после проверки.") },
            confirmButton = { TextButton(onClick = { verified = null }) { Text("Закрыть") } },
        )
    }
}
