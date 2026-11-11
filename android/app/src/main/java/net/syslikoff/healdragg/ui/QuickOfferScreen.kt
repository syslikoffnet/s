package net.syslikoff.healdragg.ui

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.lazy.LazyColumn
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
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

/** One explicitly approved command; the same ledger and live checks as batch delivery. */
@Composable
fun QuickOfferScreen(vm: AppViewModel, slug: String, onBack: () -> Unit, onOpenAccounts: () -> Unit) {
    val api = vm.api
    val scope = rememberCoroutineScope()
    var candidate by remember(slug) { mutableStateOf<JSONObject?>(null) }
    var candidateLoaded by remember(slug) { mutableStateOf(false) }
    var status by remember { mutableStateOf<JSONObject?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var sessionQuery by rememberSaveable { mutableStateOf("") }
    var session by rememberSaveable { mutableStateOf("") }
    var amount by rememberSaveable { mutableStateOf("") }
    var currency by rememberSaveable { mutableStateOf("star") }
    var language by rememberSaveable { mutableStateOf("ru") }
    var confirm by remember { mutableStateOf<JSONObject?>(null) }
    var reconcile by remember { mutableStateOf(false) }
    val sessions = vm.sessions.filter { it.active && it.purpose == "offers" &&
        (it.name.contains(sessionQuery.trim(), true) || (it.username ?: "").contains(sessionQuery.trim().removePrefix("@"), true)) }
    LaunchedEffect(api, slug) {
        if (api != null) {
            try { candidate = api.offerCandidates(JSONObject().put("slug_exact", slug).put("limit", 1)).arr("rows").optJSONObject(0) }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { error = e.message ?: "Не удалось загрузить NFT" }
            finally { candidateLoaded = true }
        }
    }
    LaunchedEffect(api) {
        val client = api ?: return@LaunchedEffect
        while (true) {
            try { status = client.deliveryStatus() }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { /* next refresh */ }
            delay(3000)
        }
    }
    val run = status?.optJSONObject("run")
    val running = run?.optString("status") == "running"
    val blocked = status?.optBoolean("restore_blocked") == true || (status?.optInt("unresolved") ?: 0) > 0
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            HdCard {
                TextButton(onClick = onBack) { Text("← Назад") }
                Text("Предложение для NFT", style = MaterialTheme.typography.headlineSmall, color = Hd.Text)
                Text(slug, color = Hd.Accent, style = MaterialTheme.typography.titleMedium)
                val item = candidate
                if (item == null) Text(if (candidateLoaded) "NFT или владелец не найдены в базе. Обновите профиль и вернитесь к карточке." else "Загружаю данные NFT…", color = Hd.Warn)
                else Text("Получатель: @${item.optString("owner_username")} · ID ${item.optLong("owner_tg_id")}", color = Hd.Text)
                Text("Команда придёт в личный чат владельца. Перед отправкой данные проверяются в Telegram; создание оффера ботом не подтверждается.", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
            }
        }
        item {
            HdCard {
                Text("Аккаунт отправки", style = MaterialTheme.typography.titleMedium, color = Hd.Text)
                TextButton(onClick = onOpenAccounts) { Text("Управление аккаунтами и @echidbot") }
                HdTextField(sessionQuery, { sessionQuery = it }, "Найти аккаунт", hint = "Имя или @username")
                Text(if (session.isEmpty()) "Выберите аккаунт" else "Выбран: $session", color = Hd.Muted)
                sessions.take(20).forEach { account ->
                    FilterChip(selected = session == account.name, onClick = { session = account.name },
                        label = { Text(account.username?.let { "@$it · ${account.name}" } ?: account.name) })
                }
                if (sessions.size > 20) Text("Показаны первые 20 — уточните поиск", color = Hd.Muted)
                if (sessions.isEmpty()) Text("Аккаунты не найдены", color = Hd.Warn)
            }
        }
        item {
            HdCard {
                Text("Сумма и язык", style = MaterialTheme.typography.titleMedium, color = Hd.Text)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf("star", "gram").forEach { value -> FilterChip(selected = currency == value, onClick = { currency = value }, label = { Text(value) }) }
                }
                HdTextField(amount, { amount = it }, "Сумма, $currency", keyboard = KeyboardType.Number)
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf("ru", "en", "zh", "ar", "ko", "ja", "tl", "th", "ms").forEach { value ->
                        FilterChip(selected = language == value, onClick = { language = value }, label = { Text(value) })
                    }
                }
                Text("Целые единицы; до 10000 star или 10 gram в сутки, не более 20 сообщений и не чаще одного в минуту. Повтор по NFT запрещён.", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
                if (blocked) {
                    Text("Нужна ручная сверка истории в Telegram.", color = Hd.Warn)
                    OutlinedButton(onClick = { reconcile = true }) { Text("Сверить историю") }
                }
                if (running) OutlinedButton(enabled = !busy, onClick = {
                    busy = true
                    scope.launch {
                        try { api?.stopDelivery(); status = api?.deliveryStatus() }
                        catch (e: CancellationException) { throw e }
                        catch (e: Exception) { error = e.message }
                        finally { busy = false }
                    }
                }) { Text("Остановить отправку") }
                else Button(enabled = !busy && !blocked && status != null && candidate != null, onClick = {
                    val item = candidate ?: return@Button
                    val n = amount.toIntOrNull()
                    if (n == null || n <= 0 || vm.sessions.none { it.name == session && it.active && it.purpose == "offers" }) {
                        error = "Выберите аккаунт и введите положительную целую сумму"
                    } else {
                        busy = true; error = null
                        scope.launch {
                            try {
                                val client = api ?: throw ApiException(0, "Движок не запущен")
                                val body = JSONObject().put("session", session).put("slug", slug).put("seller_id", item.optLong("owner_tg_id"))
                                    .put("amount", n).put("currency", currency).put("language", language)
                                confirm = client.prepareSingleDelivery(body).optJSONObject("run")
                            } catch (e: CancellationException) { throw e }
                            catch (e: Exception) { error = e.message ?: "Не удалось подготовить команду" }
                            finally { busy = false }
                        }
                    }
                }, modifier = Modifier.fillMaxWidth()) { Text(if (busy) "Подготовка…" else "Проверить и подтвердить") }
                run?.let {
                    val label = when (it.optString("status")) { "prepared" -> "подготовлена"; "running" -> "отправляется"; "done" -> "завершена"; "stopped" -> "остановлена"; "interrupted" -> "прервана"; else -> "ошибка" }
                    Text("Последняя отправка: $label", color = Hd.Muted)
                    it.str("error")?.let { message -> Text(message, color = Hd.Warn) }
                }
                error?.let { Text(it, color = Hd.Bad) }
            }
        }
    }
    confirm?.let { prepared ->
        val item = prepared.arr("rows").optJSONObject(0)
        AlertDialog(onDismissRequest = { if (!busy) confirm = null }, title = { Text("Отправить команду?") },
            text = { Text("Аккаунт: ${prepared.optString("session")}\nПолучатель ID ${item?.optLong("seller_id")}\n\n${item?.optString("command")}\n\nКоманда будет отправлена в личный чат владельца после онлайн-проверки. Уже отправленное сообщение отозвать нельзя.") },
            confirmButton = { TextButton(enabled = !busy, onClick = {
                busy = true
                scope.launch {
                    try { val client = api ?: throw ApiException(0, "Движок не запущен"); client.startDelivery(prepared.optString("id")); status = client.deliveryStatus(); confirm = null }
                    catch (e: CancellationException) { throw e }
                    catch (e: Exception) { error = e.message ?: "Не удалось запустить отправку"; confirm = null }
                    finally { busy = false }
                }
            }) { Text("Отправить") } }, dismissButton = { TextButton(enabled = !busy, onClick = { confirm = null }) { Text("Отмена") } })
    }
    if (reconcile) AlertDialog(onDismissRequest = { reconcile = false }, title = { Text("Сверка истории") },
        text = { Text("Проверьте в Telegram все неопределённые попытки и возможные отправки после восстановления копии. Повтор по тому же NFT останется запрещён.") },
        confirmButton = { TextButton(onClick = {
            reconcile = false
            scope.launch {
                try { api?.acknowledgeDelivery(); status = api?.deliveryStatus() }
                catch (e: CancellationException) { throw e }
                catch (e: Exception) { error = e.message }
            }
        }) { Text("Проверил") } }, dismissButton = { TextButton(onClick = { reconcile = false }) { Text("Отмена") } })
}
