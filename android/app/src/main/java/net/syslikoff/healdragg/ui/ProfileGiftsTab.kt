package net.syslikoff.healdragg.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** Только сохранённые наблюдения: отправленные подарки видны лишь в собранных профилях получателей. */
@Composable
fun ProfileGiftsTab(vm: AppViewModel, tgId: Long, onToast: (String) -> Unit, onOpenProfile: (String) -> Unit,
    onOffer: (String) -> Unit) {
    val api = vm.api
    val scope = rememberCoroutineScope()
    var direction by remember(tgId) { mutableStateOf("received") }
    var rows by remember(tgId, direction) { mutableStateOf<List<GiftItem>>(emptyList()) }
    var cursors by remember(tgId, direction) { mutableStateOf(listOf<Long?>(null)) }
    var pageIndex by remember(tgId, direction) { mutableStateOf(0) }
    var next by remember(tgId, direction) { mutableStateOf<Long?>(null) }
    var loading by remember(tgId, direction) { mutableStateOf(false) }
    var pageError by remember(tgId, direction) { mutableStateOf<String?>(null) }
    var taskMessage by remember(tgId) { mutableStateOf<String?>(null) }
    var reload by remember(tgId) { mutableStateOf(0) }
    var job by remember(tgId) { mutableStateOf<Job?>(null) }
    var starting by remember(tgId) { mutableStateOf(false) }
    var stopping by remember(tgId) { mutableStateOf(false) }
    val before = cursors.getOrNull(pageIndex)

    LaunchedEffect(tgId, direction, before, reload, api) {
        val client = api
        rows = emptyList()
        next = null
        pageError = null
        if (client == null) { pageError = "Движок не запущен"; return@LaunchedEffect }
        loading = true
        try {
            val page = client.profileGifts(tgId, direction, before)
            rows = page.rows
            next = page.nextBefore
        } catch (exc: CancellationException) { throw exc }
        catch (exc: Exception) { pageError = exc.message ?: "Не удалось загрузить подарки" }
        finally { loading = false }
    }
    LaunchedEffect(tgId, api) {
        val client = api ?: return@LaunchedEffect
        try {
            val active = client.jobs().firstOrNull { it.kind == "gifts" && it.targetId == tgId && !it.finished }
            if (job == null && !starting) job = active
        } catch (exc: CancellationException) { throw exc }
        catch (_: Exception) { /* Сохранённая задача восстановится после переподключения движка. */ }
    }
    LaunchedEffect(job?.key, api) {
        val key = job?.key ?: return@LaunchedEffect
        val client = api ?: return@LaunchedEffect
        while (true) {
            delay(2_000)
            val response = try { client.jobs() } catch (exc: CancellationException) { throw exc }
                catch (_: Exception) { continue }
            val found = response.firstOrNull { it.key == key }
            if (found == null) {
                taskMessage = "Не удалось получить статус сбора. Проверьте сохранённые задачи в разделе «Подарки»."
                job = null
                reload++
                break
            }
            job = found
            if (found.finished) {
                val counts = found.state?.obj("queue")
                when {
                    found.phase == "отменено" -> taskMessage = "Сбор остановлен; сохранённые записи остались в базе."
                    found.error != null -> taskMessage = found.error
                    (counts?.optInt("deferred") ?: 0) > 0 -> taskMessage = "Большой профиль отложен. Откройте раздел «Подарки» для полного сбора."
                    (counts?.optInt("unavailable") ?: 0) > 0 -> taskMessage = "Профиль недоступен аккаунту Telegram; данные могут быть неполными."
                    else -> onToast("Сбор завершён: ${found.found} записей")
                }
                pageIndex = 0
                cursors = listOf(null)
                reload++
                job = null
                break
            }
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Записи подарков", style = MaterialTheme.typography.titleSmall, color = Hd.Text)
        Text("Полученные — наблюдения в этом профиле. Отправленные — только записи из уже собранных профилей других людей. Скрытые дарители неизвестны; NFT могли сменить владельца.", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            FilterChip(selected = direction == "received", onClick = { direction = "received" }, label = { Text("Полученные") })
            FilterChip(selected = direction == "sent", onClick = { direction = "sent" }, label = { Text("Отправленные") })
        }
        Button(onClick = {
            starting = true
            taskMessage = null
            scope.launch {
                try { job = api?.collectProfileGifts(tgId) ?: throw ApiException(0, "Движок не запущен") }
                catch (exc: CancellationException) { throw exc }
                catch (exc: Exception) { taskMessage = exc.message ?: "Не удалось запустить сбор" }
                finally { starting = false }
            }
        }, enabled = !starting && job == null) { Text(if (starting) "Запуск…" else "Собрать подарки профиля") }
        job?.let { active ->
            Text("${active.phase} · ${active.done}/${active.total} · найдено ${active.found}", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
            TextButton(enabled = !stopping && !active.finished, onClick = {
                stopping = true
                scope.launch {
                    try {
                        if (api?.cancelJob(active.key) != true) taskMessage = "Задача уже завершилась или недоступна."
                    } catch (exc: CancellationException) { throw exc }
                    catch (exc: Exception) { taskMessage = exc.message ?: "Не удалось остановить сбор" }
                    finally { stopping = false }
                }
            }) { Text(if (stopping) "Останавливаю…" else "Остановить сбор") }
        }
        taskMessage?.let { message ->
            HdCard {
                Text(message, color = Hd.Warn, style = MaterialTheme.typography.bodySmall)
                TextButton(onClick = { taskMessage = null }) { Text("Понятно") }
            }
        }
        pageError?.let { ErrorBanner(it, onRetry = { reload++ }) }
        if (loading) LoadingBlock()
        if (!loading && pageError == null && rows.isEmpty()) Text("Записей пока нет; это не означает, что подарков нет.", color = Hd.Muted)
        rows.forEach { gift ->
            GiftCard(gift, api, onToast, onOpenProfile, onOffer)
            if (!gift.nameHidden && gift.fromTgId > 0 && gift.fromTgId != tgId) {
                OutlinedButton(onClick = { onOpenProfile(gift.fromTgId.toString()) }) { Text("Открыть указанного дарителя") }
            }
            if (gift.ownerTgId > 0 && gift.ownerTgId != tgId) {
                OutlinedButton(onClick = { onOpenProfile(gift.ownerTgId.toString()) }) { Text("Открыть профиль с записью") }
            }
        }
        if (pageIndex > 0 || next != null) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                OutlinedButton(onClick = { pageIndex-- }, enabled = pageIndex > 0 && !loading, modifier = Modifier.weight(1f)) { Text("Назад") }
                OutlinedButton(onClick = {
                    val cursor = next
                    if (cursor != null) {
                        cursors = cursors.take(pageIndex + 1) + cursor
                        pageIndex++
                    }
                }, enabled = next != null && !loading, modifier = Modifier.weight(1f)) { Text("Далее") }
            }
            Text("Страница ${pageIndex + 1} · по порядку сохранения. Новые записи появятся после обновления.", color = Hd.Dim, style = MaterialTheme.typography.labelSmall)
        }
        Spacer(Modifier.height(8.dp))
    }
}
