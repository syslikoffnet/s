package net.syslikoff.healdragg.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
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
fun ProfileGiftsTab(vm: AppViewModel, tgId: Long, onToast: (String) -> Unit, onOpenProfile: (String) -> Unit) {
    val api = vm.api
    val scope = rememberCoroutineScope()
    var direction by remember(tgId) { mutableStateOf("received") }
    var rows by remember(tgId, direction) { mutableStateOf<List<GiftItem>>(emptyList()) }
    var more by remember(tgId, direction) { mutableStateOf(false) }
    var loading by remember(tgId, direction) { mutableStateOf(false) }
    var error by remember(tgId, direction) { mutableStateOf<String?>(null) }
    var reload by remember(tgId) { mutableStateOf(0) }
    var job by remember(tgId) { mutableStateOf<Job?>(null) }
    var starting by remember(tgId) { mutableStateOf(false) }

    suspend fun load(reset: Boolean) {
        val client = api ?: run { error = "Движок не запущен"; return }
        if (loading) return
        loading = true
        try {
            val page = client.profileGifts(tgId, direction, if (reset) 0 else rows.size)
            rows = if (reset) page.rows else (rows + page.rows).distinctBy { it.id }
            more = page.hasMore
            error = null
        } catch (exc: CancellationException) {
            throw exc
        } catch (exc: Exception) {
            error = exc.message ?: "Не удалось загрузить подарки"
        } finally { loading = false }
    }
    LaunchedEffect(tgId, direction, reload, api) { load(true) }
    LaunchedEffect(tgId, api) {
        val client = api ?: return@LaunchedEffect
        runCatching { client.jobs().firstOrNull { it.kind == "gifts" && it.targetId == tgId && !it.finished } }
            .onSuccess { if (job == null && !starting) job = it }
    }
    LaunchedEffect(job?.key) {
        val key = job?.key ?: return@LaunchedEffect
        val client = api ?: return@LaunchedEffect
        while (true) {
            delay(2_000)
            val response = try { client.jobs() } catch (exc: CancellationException) { throw exc }
                catch (_: Exception) { continue }
            val found = response.firstOrNull { it.key == key }
            if (found == null) { job = null; reload++; break }
            job = found
            if (found.finished) {
                val counts = found.state?.obj("queue")
                when {
                    found.error != null -> error = found.error
                    (counts?.optInt("deferred") ?: 0) > 0 -> error = "Большой профиль отложен. Откройте раздел «Подарки» для полного сбора."
                    (counts?.optInt("unavailable") ?: 0) > 0 -> error = "Профиль недоступен аккаунту Telegram; данные могут быть неполными."
                    else -> onToast("Сбор завершён: ${found.found} записей")
                }
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
            scope.launch {
                try {
                    job = api?.collectProfileGifts(tgId)
                        ?: throw ApiException(0, "Движок не запущен")
                } catch (exc: CancellationException) { throw exc }
                catch (exc: Exception) { error = exc.message }
                finally { starting = false }
            }
        }, enabled = !starting && job == null) { Text(if (starting) "Запуск…" else "Собрать подарки профиля") }
        job?.let { Text("${it.phase} · ${it.done}/${it.total} · найдено ${it.found}", color = Hd.Muted, style = MaterialTheme.typography.bodySmall) }
        error?.let { ErrorBanner(it, onRetry = { reload++ }) }
        if (loading && rows.isEmpty()) LoadingBlock()
        if (!loading && error == null && rows.isEmpty()) Text("Записей пока нет; это не означает, что подарков нет.", color = Hd.Muted)
        rows.forEach { gift ->
            GiftCard(gift, api, onToast, onOpenProfile)
            if (!gift.nameHidden && gift.fromTgId > 0 && gift.fromTgId != tgId) {
                OutlinedButton(onClick = { onOpenProfile(gift.fromTgId.toString()) }) { Text("Открыть указанного дарителя") }
            }
            if (gift.ownerTgId > 0 && gift.ownerTgId != tgId) {
                OutlinedButton(onClick = { onOpenProfile(gift.ownerTgId.toString()) }) { Text("Открыть профиль с записью") }
            }
        }
        if (more) OutlinedButton(onClick = { scope.launch { load(false) } }, enabled = !loading, modifier = Modifier.fillMaxWidth()) { Text(if (loading) "Загружаю…" else "Показать ещё") }
        Spacer(Modifier.height(8.dp).padding(bottom = 2.dp))
    }
}
