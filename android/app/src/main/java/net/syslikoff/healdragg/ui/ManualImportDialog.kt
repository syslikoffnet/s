package net.syslikoff.healdragg.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Ручное пополнение базы: вставляешь список ссылок и @username, приложение
 * проверяет его локально, показывает долю ошибок, а по кнопке — резолвит через
 * Telegram и добавляет чаты. Вступления по желанию: сразу в архив и без звука.
 */
@Composable
fun ManualImportDialog(
    vm: AppViewModel,
    onDismiss: () -> Unit,
    onImported: () -> Unit,
    onToast: (String) -> Unit,
) {
    val clipboard = LocalClipboardManager.current
    val scope = rememberCoroutineScope()
    var text by rememberSaveable { mutableStateOf("") }
    var preview by remember { mutableStateOf<ImportPreview?>(null) }
    var parsing by remember { mutableStateOf(false) }
    var starting by remember { mutableStateOf(false) }
    var progress by remember { mutableStateOf<Job?>(null) }
    var outcome by remember { mutableStateOf<ImportOutcome?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var join by rememberSaveable { mutableStateOf(true) }
    var archive by rememberSaveable { mutableStateOf(true) }

    fun check() {
        val source = text.trim()
        if (source.isEmpty() || parsing) return
        parsing = true
        error = null
        vm.parseImport(source) { result ->
            parsing = false
            result.onSuccess { preview = it }.onFailure { error = it.message }
        }
    }

    fun start() {
        val source = text.trim()
        if (source.isEmpty() || starting || progress != null) return
        starting = true
        error = null
        outcome = null
        vm.importGroups(source, join, archive) { result ->
            starting = false
            result.onSuccess {
                progress = it
                onToast("Проверяю список…")
            }.onFailure { error = it.message ?: "Не удалось запустить импорт" }
        }
    }

    LaunchedEffect(progress?.key) {
        val key = progress?.key ?: return@LaunchedEffect
        val client = vm.api ?: return@LaunchedEffect
        while (true) {
            delay(1_500)
            val fresh = runCatching { client.jobs().firstOrNull { it.key == key } }.getOrNull()
            if (fresh == null) {
                progress = null
                break
            }
            progress = fresh
            if (fresh.finished) {
                if (fresh.error != null) {
                    error = fresh.error
                } else {
                    val result = importOutcomeFrom(fresh.state)
                    outcome = result
                    onImported()
                    onToast(
                        when {
                            result == null -> "Импорт завершён"
                            result.added > 0 -> "Добавлено чатов: ${result.added}"
                            else -> "Новых чатов нет — всё уже в базе"
                        },
                    )
                }
                delay(1_200)
                progress = null
                break
            }
        }
    }

    // Разбор локальный и быстрый: обновляем превью, пока пользователь печатает.
    LaunchedEffect(text) {
        if (text.isBlank()) {
            preview = null
            return@LaunchedEffect
        }
        delay(600)
        check()
    }

    val running = progress != null && !progress!!.finished
    val stats = preview?.stats

    Dialog(
        onDismissRequest = { if (!running) onDismiss() },
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Surface(
            color = Hd.Bg,
            contentColor = Hd.Text,
            shape = RoundedCornerShape(24.dp),
            modifier = Modifier
                .padding(12.dp)
                .fillMaxWidth()
                .heightIn(max = 720.dp),
        ) {
            Column(
                Modifier
                    .verticalScroll(rememberScrollState())
                    .padding(18.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        Modifier
                            .size(40.dp)
                            .clip(RoundedCornerShape(12.dp))
                            .background(Hd.Accent.copy(alpha = 0.16f)),
                        contentAlignment = Alignment.Center,
                    ) {
                        Icon(Icons.Filled.Add, contentDescription = null, tint = Hd.Accent)
                    }
                    Spacer(Modifier.width(12.dp))
                    Column(Modifier.weight(1f)) {
                        Text(
                            "Добавить вручную",
                            style = MaterialTheme.typography.titleMedium,
                            color = Hd.Text,
                        )
                        Text(
                            "Ссылки t.me, @username, инвайты и ID",
                            style = MaterialTheme.typography.bodySmall,
                            color = Hd.Muted,
                        )
                    }
                    IconButton(onClick = { if (!running) onDismiss() }) {
                        Icon(Icons.Filled.Close, contentDescription = "Закрыть", tint = Hd.Muted)
                    }
                }

                Spacer(Modifier.height(14.dp))
                HdTextField(
                    value = text,
                    onValueChange = {
                        text = it
                        error = null
                        outcome = null
                    },
                    label = "Список чатов",
                    hint = "https://t.me/memachh\n@beeeelogvardeyskaya\nt.me/+AbCdEfGhIjK",
                    singleLine = false,
                    minLines = 5,
                )

                Row(
                    Modifier.fillMaxWidth().padding(top = 6.dp),
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    TextButton(
                        onClick = {
                            val pasted = clipboard.getText()?.text.orEmpty()
                            if (pasted.isBlank()) {
                                onToast("Буфер обмена пуст")
                            } else {
                                text = if (text.isBlank()) pasted else text.trimEnd() + "\n" + pasted
                                error = null
                            }
                        },
                        contentPadding = PaddingValues(horizontal = 8.dp),
                    ) { Text("Вставить", color = Hd.Accent2) }
                    TextButton(
                        onClick = { text = ""; preview = null; outcome = null; error = null },
                        enabled = text.isNotEmpty(),
                        contentPadding = PaddingValues(horizontal = 8.dp),
                    ) { Text("Очистить", color = Hd.Muted) }
                    Spacer(Modifier.weight(1f))
                    if (parsing) {
                        CircularProgressIndicator(
                            color = Hd.Accent,
                            strokeWidth = 2.dp,
                            modifier = Modifier.size(16.dp),
                        )
                    }
                    TextButton(onClick = { check() }, enabled = text.isNotBlank() && !parsing) {
                        Text("Проверить", color = Hd.Accent, fontWeight = FontWeight.SemiBold)
                    }
                }

                preview?.let { data ->
                    Spacer(Modifier.height(10.dp))
                    HdCard(contentPadding = PaddingValues(14.dp)) {
                        Row(
                            Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Chip("${data.stats.valid} готово", Hd.Good)
                            if (data.stats.invalid > 0) {
                                Chip("${data.stats.invalid} ошибок", Hd.Bad)
                            }
                            if (data.stats.duplicates > 0) {
                                Chip("${data.stats.duplicates} дублей", Hd.Muted)
                            }
                        }
                        Spacer(Modifier.height(8.dp))
                        Text(
                            buildString {
                                append("публичных: ${data.stats.usernames}")
                                append(" · по инвайту: ${data.stats.invites}")
                                append(" · по ID: ${data.stats.ids}")
                                append(" · всего строк: ${data.stats.total}")
                            },
                            style = MaterialTheme.typography.labelSmall,
                            color = Hd.Muted,
                        )
                        if (data.stats.invalid > 0) {
                            Spacer(Modifier.height(8.dp))
                            Text(
                                "Не распознано: ${data.stats.invalidPercent}%",
                                style = MaterialTheme.typography.labelMedium,
                                color = Hd.Bad,
                            )
                            data.invalid.take(4).forEach { issue ->
                                Text(
                                    "• ${issue.raw}${issue.reason?.let { " — $it" } ?: ""}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = Hd.Dim,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                        }
                        if (data.items.isNotEmpty()) {
                            Spacer(Modifier.height(8.dp))
                            Text(
                                data.items.take(3).joinToString(" · ") { it.raw },
                                style = MaterialTheme.typography.labelSmall,
                                color = Hd.Dim,
                                maxLines = 2,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                        if (data.sessions.isEmpty()) {
                            Spacer(Modifier.height(8.dp))
                            Text(
                                "Аккаунт-сканер не подключён: разбор работает, а проверка через " +
                                    "Telegram — нет. Сначала войди в Telegram.",
                                style = MaterialTheme.typography.labelSmall,
                                color = Hd.Warn,
                            )
                        }
                    }
                }

                Spacer(Modifier.height(10.dp))
                HdCard(contentPadding = PaddingValues(14.dp)) {
                    SwitchRow(
                        title = "Вступить в публичные чаты",
                        subtitle = "Публичные — по @username, закрытые — по инвайт-ссылке",
                        checked = join,
                        onCheckedChange = { join = it },
                    )
                    Spacer(Modifier.height(6.dp))
                    SwitchRow(
                        title = "Тихо: в архив и без уведомлений",
                        subtitle = "После вступления чат уходит в архив, звук и превью выключены",
                        checked = archive && join,
                        enabled = join,
                        onCheckedChange = { archive = it },
                    )
                }

                progress?.let { current ->
                    Spacer(Modifier.height(10.dp))
                    HdCard(contentPadding = PaddingValues(14.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            CircularProgressIndicator(
                                color = Hd.Accent,
                                strokeWidth = 2.dp,
                                modifier = Modifier.size(18.dp),
                            )
                            Spacer(Modifier.width(10.dp))
                            Column(Modifier.weight(1f)) {
                                Text(
                                    if (current.finished) "Готово" else "Проверяю через Telegram",
                                    style = MaterialTheme.typography.titleSmall,
                                    color = Hd.Text,
                                )
                                Text(
                                    buildString {
                                        append(current.phase)
                                        if (current.total > 0) append(" · ${current.done}/${current.total}")
                                        if (current.found > 0) append(" · новых ${current.found}")
                                    },
                                    style = MaterialTheme.typography.labelSmall,
                                    color = Hd.Muted,
                                )
                            }
                            TextButton(
                                onClick = {
                                    val key = current.key
                                    scope.launch {
                                        vm.api?.cancelJob(key)
                                        onToast("Импорт отменён")
                                    }
                                },
                            ) { Text("Стоп", color = Hd.Bad) }
                        }
                        if (current.total > 0) {
                            Spacer(Modifier.height(8.dp))
                            LinearProgressIndicator(
                                progress = { current.percent },
                                color = Hd.Accent,
                                trackColor = Hd.Stroke,
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                    }
                }

                error?.let { message ->
                    Spacer(Modifier.height(10.dp))
                    ErrorBanner(message = message)
                }

                outcome?.let { result ->
                    Spacer(Modifier.height(10.dp))
                    HdCard(contentPadding = PaddingValues(14.dp)) {
                        Text(
                            "Итог импорта",
                            style = MaterialTheme.typography.titleSmall,
                            color = Hd.Text,
                        )
                        Spacer(Modifier.height(8.dp))
                        Row(
                            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            StatTile("+${result.added}", "добавлено", Hd.Good)
                            StatTile("${result.existing}", "уже было", Hd.Muted)
                            if (result.joined > 0) StatTile("${result.joined}", "вступил", Hd.Accent)
                        }
                        Spacer(Modifier.height(8.dp))
                        Text(
                            buildString {
                                append("каналов: ${result.channels}")
                                append(" · групп: ${result.groups}")
                                append(" · закрытых: ${result.private}")
                                if (result.invalid > 0) append(" · битых строк: ${result.invalid}")
                                if (result.errors > 0) append(" · ошибок: ${result.errors}")
                            },
                            style = MaterialTheme.typography.labelSmall,
                            color = Hd.Muted,
                        )
                        if (result.needJoin > 0) {
                            Spacer(Modifier.height(8.dp))
                            Text(
                                "Нужно вступить: ${result.needJoin}. Включи «Вступить в публичные чаты» " +
                                    "и повтори — приложение сделает это тихо.",
                                style = MaterialTheme.typography.labelSmall,
                                color = Hd.Warn,
                            )
                        }
                        if (result.requested > 0) {
                            Spacer(Modifier.height(6.dp))
                            Text(
                                "Заявок отправлено: ${result.requested} — ждём одобрения админов.",
                                style = MaterialTheme.typography.labelSmall,
                                color = Hd.Accent2,
                            )
                        }
                        val failed = result.rows.filter {
                            it.status == "error" || it.status == "need_join" || it.status == "invalid"
                        }
                        failed.take(5).forEach { row ->
                            Spacer(Modifier.height(4.dp))
                            Text(
                                "• ${row.title ?: row.raw} — ${row.reason ?: row.status}",
                                style = MaterialTheme.typography.labelSmall,
                                color = Hd.Dim,
                                maxLines = 2,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                }

                Spacer(Modifier.height(14.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(
                        onClick = { start() },
                        enabled = (stats?.valid ?: 0) > 0 && !starting && !running,
                        shape = RoundedCornerShape(14.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = Hd.Accent,
                            contentColor = Hd.Black,
                        ),
                        modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                    ) {
                        if (starting || running) {
                            CircularProgressIndicator(
                                color = Hd.Black,
                                strokeWidth = 2.dp,
                                modifier = Modifier.size(16.dp),
                            )
                            Spacer(Modifier.width(8.dp))
                        }
                        Text(
                            when {
                                starting -> "Запускаю…"
                                running -> "Идёт импорт…"
                                else -> "Добавить в базу"
                            },
                            fontWeight = FontWeight.SemiBold,
                        )
                    }
                    TextButton(
                        onClick = { if (!running) onDismiss() },
                        modifier = Modifier.heightIn(min = 48.dp),
                    ) { Text("Закрыть", color = Hd.Muted) }
                }
                if (stats != null && stats.valid > 0 && !running) {
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "Проверю ${stats.valid} чатов через Telegram. Если аккаунт не подключён, " +
                            "импорт попросит войти.",
                        style = MaterialTheme.typography.labelSmall,
                        color = Hd.Dim,
                    )
                }
                Spacer(Modifier.height(4.dp))
            }
        }
    }
}

@Composable
private fun SwitchRow(
    title: String,
    subtitle: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    enabled: Boolean = true,
) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(
                title,
                style = MaterialTheme.typography.titleSmall,
                color = if (enabled) Hd.Text else Hd.Dim,
            )
            Text(
                subtitle,
                style = MaterialTheme.typography.labelSmall,
                color = Hd.Dim,
            )
        }
        Spacer(Modifier.width(8.dp))
        Switch(
            checked = checked,
            onCheckedChange = onCheckedChange,
            enabled = enabled,
            colors = SwitchDefaults.colors(
                checkedThumbColor = Hd.Black,
                checkedTrackColor = Hd.Accent,
                uncheckedThumbColor = Hd.Muted,
                uncheckedTrackColor = Hd.Surface2,
                uncheckedBorderColor = Hd.Stroke,
            ),
        )
    }
}
