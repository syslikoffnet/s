package net.syslikoff.healdragg.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Composable
fun TargetsScreen(
    vm: AppViewModel,
    onOpenTarget: (Long) -> Unit,
    onToast: (String) -> Unit,
    onOpenAccounts: () -> Unit = {},
) {
    var query by rememberSaveable { mutableStateOf("") }
    var addOpen by rememberSaveable { mutableStateOf(false) }
    var toDelete by remember { mutableStateOf<Target?>(null) }

    val filtered = remember(vm.targets, query) {
        val needle = query.trim().lowercase()
        if (needle.isBlank()) vm.targets
        else vm.targets.filter { target ->
            target.displayName.lowercase().contains(needle) ||
                target.username?.lowercase()?.contains(needle) == true ||
                target.tgId.toString().contains(needle)
        }
    }

    LaunchedEffect(Unit) {
        if (vm.targets.isEmpty()) vm.refresh(force = true)
    }

    Box(Modifier.fillMaxSize()) {
        RefreshableBox(
            refreshing = vm.refreshing,
            onRefresh = { vm.refresh(force = true) },
            modifier = Modifier.fillMaxSize(),
        ) {
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 6.dp, bottom = 96.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                item("stats") { StatsStrip(vm.stats) }
                item("search") {
                    OutlinedTextField(
                        value = query,
                        onValueChange = { query = it },
                        placeholder = {
                            Text("Поиск по имени, @username или ID", color = Hd.Dim, style = MaterialTheme.typography.bodySmall)
                        },
                        leadingIcon = {
                            Icon(Icons.Filled.Search, contentDescription = null, tint = Hd.Dim)
                        },
                        trailingIcon = {
                            if (query.isNotEmpty()) {
                                IconButton(onClick = { query = "" }) {
                                    Icon(Icons.Filled.Close, contentDescription = "Очистить поиск", tint = Hd.Dim)
                                }
                            }
                        },
                        singleLine = true,
                        shape = RoundedCornerShape(14.dp),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = Hd.Accent,
                            unfocusedBorderColor = Hd.Stroke,
                            focusedContainerColor = Hd.Surface,
                            unfocusedContainerColor = Hd.Surface,
                            cursorColor = Hd.Accent,
                            focusedTextColor = Hd.Text,
                            unfocusedTextColor = Hd.Text,
                        ),
                        modifier = Modifier.fillMaxWidth(),
                    )
                }

                vm.lastError?.takeIf { vm.targets.isEmpty() }?.let { message ->
                    item("error") {
                        ErrorBanner(message = message, onRetry = { vm.refresh(force = true) })
                    }
                }

                if (vm.loading && vm.targets.isEmpty()) {
                    item("loading") { LoadingBlock() }
                }

                if (!vm.hasSessions) {
                    item("no-account") {
                        HdCard {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text("🔐", fontSize = 18.sp)
                                Spacer(Modifier.width(10.dp))
                                Column(Modifier.weight(1f)) {
                                    Text(
                                        "Telegram-аккаунт не подключён",
                                        style = MaterialTheme.typography.titleSmall,
                                        color = Hd.Warn,
                                    )
                                    Text(
                                        "Без него нельзя добавить цель и запустить скан.",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = Hd.Muted,
                                    )
                                }
                                TextButton(onClick = onOpenAccounts) {
                                    Text("Войти", color = Hd.Accent2)
                                }
                            }
                        }
                    }
                }

                if (!vm.loading && filtered.isEmpty()) {
                    item("empty") {
                        EmptyState(
                            emoji = when {
                                !vm.hasSessions -> "🔐"
                                query.isBlank() -> "🎯"
                                else -> "🔍"
                            },
                            title = when {
                                !vm.hasSessions -> "Сначала аккаунт"
                                query.isBlank() -> "Пока нет целей"
                                else -> "Ничего не найдено"
                            },
                            text = when {
                                !vm.hasSessions ->
                                    "Приложение читает Telegram от имени твоего аккаунта — подключи его, это минута."
                                query.isBlank() ->
                                    "Добавь цель по @username, ссылке или ID — приложение найдёт её чаты и собеседников."
                                else -> "Попробуй другое имя, @username или числовой ID."
                            },
                            actionLabel = when {
                                !vm.hasSessions -> "Войти в Telegram"
                                query.isBlank() -> "Добавить цель"
                                else -> null
                            },
                            onAction = when {
                                !vm.hasSessions -> ({ onOpenAccounts() })
                                query.isBlank() -> ({ addOpen = true })
                                else -> null
                            },
                        )
                    }
                }

                items(filtered, key = { it.tgId }) { target ->
                    TargetCard(
                        target = target,
                        onOpen = { onOpenTarget(target.tgId) },
                        onScan = {
                            vm.startScan(target.tgId, deep = false) { result ->
                                result.onSuccess {
                                    onToast("Скан «${target.displayName}» запущен")
                                }.onFailure {
                                    onToast(it.message ?: "Не удалось запустить скан")
                                }
                            }
                        },
                        onDelete = { toDelete = target },
                    )
                }
            }
        }

        ExtendedFloatingActionButton(
            onClick = { if (vm.hasSessions) addOpen = true else onOpenAccounts() },
            containerColor = Hd.Accent,
            contentColor = Hd.Black,
            icon = { Icon(Icons.Filled.Add, contentDescription = null) },
            text = { Text("Цель", fontWeight = FontWeight.SemiBold) },
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(18.dp),
        )
    }

    if (addOpen) {
        AddTargetDialog(
            vm = vm,
            onDismiss = { addOpen = false },
            onAdded = { target ->
                addOpen = false
                onToast("Цель добавлена: ${target.displayName}")
            },
            onToast = onToast,
        )
    }

    toDelete?.let { target ->
        AlertDialog(
            onDismissRequest = { toDelete = null },
            title = { Text("Удалить цель?") },
            text = {
                Text(
                    "«${target.displayName}» и все её чаты, сообщения и алерты исчезнут из базы. " +
                        "Аккаунт в Telegram останется как есть.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Hd.Muted,
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    val id = target.tgId
                    toDelete = null
                    vm.deleteTarget(id) { ok ->
                        onToast(if (ok) "Удалено" else "Не удалось удалить")
                    }
                }) { Text("Удалить", color = Hd.Bad) }
            },
            dismissButton = {
                TextButton(onClick = { toDelete = null }) { Text("Отмена", color = Hd.Muted) }
            },
            containerColor = Hd.Surface,
            titleContentColor = Hd.Text,
            textContentColor = Hd.Muted,
        )
    }
}

@Composable
private fun StatsStrip(stats: Stats?) {
    val data = stats ?: return
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        StatTile(value = compactNumber(data.groups), label = "групп в базе", tint = Hd.Accent2)
        StatTile(value = compactNumber(data.targets), label = "целей", tint = Hd.Accent)
        StatTile(value = compactNumber(data.sessions), label = "сессий", tint = Hd.Good)
        StatTile(value = data.dbHuman, label = "база", tint = Hd.Text)
    }
}

@Composable
private fun TargetCard(
    target: Target,
    onOpen: () -> Unit,
    onScan: () -> Unit,
    onDelete: () -> Unit,
) {
    var menu by remember { mutableStateOf(false) }

    HdCard(onClick = onOpen) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Avatar(target.initials, target.tgId, size = 48)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        target.displayName,
                        style = MaterialTheme.typography.titleMedium,
                        color = Hd.Text,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                    if (target.isBot) {
                        Spacer(Modifier.width(6.dp))
                        Chip("бот", Hd.Accent2)
                    }
                }
                Spacer(Modifier.height(2.dp))
                Text(
                    text = "${target.handle} · ${target.tgId}",
                    style = MaterialTheme.typography.labelMedium,
                    color = Hd.Dim,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            if (target.alertsUnread > 0) {
                Box(
                    Modifier
                        .background(Hd.Bad.copy(alpha = 0.16f), RoundedCornerShape(50))
                        .padding(horizontal = 8.dp, vertical = 3.dp),
                ) {
                    Text(
                        "🔔 ${target.alertsUnread}",
                        color = Hd.Bad,
                        style = MaterialTheme.typography.labelMedium,
                    )
                }
                Spacer(Modifier.width(4.dp))
            }
            Box {
                IconButton(onClick = { menu = true }) {
                    Icon(Icons.Filled.MoreVert, contentDescription = "Действия с целью", tint = Hd.Muted)
                }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    DropdownMenuItem(
                        text = { Text("Сканировать", color = Hd.Text) },
                        leadingIcon = { Icon(Icons.Filled.PlayArrow, contentDescription = null, tint = Hd.Good) },
                        onClick = {
                            menu = false
                            onScan()
                        },
                    )
                    DropdownMenuItem(
                        text = { Text("Удалить", color = Hd.Bad) },
                        leadingIcon = { Icon(Icons.Filled.Delete, contentDescription = null, tint = Hd.Bad) },
                        onClick = {
                            menu = false
                            onDelete()
                        },
                    )
                }
            }
        }
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Chip("💬 ${target.groupsCount}", Hd.Accent2, emoji = null)
            Chip("✉️ ${compactNumber(target.activity.messages)}", Hd.Muted)
            Chip("👥 ${compactNumber(target.activity.peers)}", Hd.Muted)
            Chip(
                text = target.lastScan ?: "не сканировалась",
                color = if (target.lastScanAt > 0) Hd.Good else Hd.Dim,
            )
        }
    }
}

@Composable
private fun AddTargetDialog(
    vm: AppViewModel,
    onDismiss: () -> Unit,
    onAdded: (Target) -> Unit,
    onToast: (String) -> Unit,
) {
    var query by rememberSaveable { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    AlertDialog(
        onDismissRequest = { if (!busy) onDismiss() },
        title = { Text("Новая цель") },
        text = {
            Column {
                Text(
                    "Вставь @username, ссылку t.me/… или числовой ID. Приложение найдёт аккаунт " +
                        "и проверит, в каких публичных чатах он состоит.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Hd.Muted,
                )
                Spacer(Modifier.height(12.dp))
                HdTextField(
                    value = query,
                    onValueChange = {
                        query = it
                        error = null
                    },
                    label = "Цель",
                    hint = "@durov или https://t.me/durov",
                    isError = error != null,
                    singleLine = true,
                )
                error?.let {
                    Spacer(Modifier.height(8.dp))
                    Text(it, color = Hd.Bad, style = MaterialTheme.typography.bodySmall)
                    if (it.contains("аккаунт", ignoreCase = true)) {
                        TextButton(
                            onClick = {
                                onDismiss()
                                onOpenAccounts()
                            },
                            contentPadding = PaddingValues(0.dp),
                        ) {
                            Text(
                                "Войти в Telegram",
                                color = Hd.Accent2,
                                style = MaterialTheme.typography.labelLarge,
                            )
                        }
                    }
                }
                if (busy) {
                    Spacer(Modifier.height(12.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(
                            color = Hd.Accent,
                            strokeWidth = 2.dp,
                            modifier = Modifier.size(16.dp),
                        )
                        Spacer(Modifier.width(8.dp))
                        Text("Ищу аккаунт и чаты…", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                enabled = query.isNotBlank() && !busy,
                onClick = {
                    busy = true
                    error = null
                    vm.addTarget(query.trim()) { result ->
                        busy = false
                        result.onSuccess { target ->
                            query = ""
                            onAdded(target)
                        }.onFailure { exc ->
                            error = exc.message ?: "Не удалось добавить цель"
                            if (vm.targets.isNotEmpty()) onToast("Проверь @username или ID")
                        }
                    }
                },
            ) {
                Text(if (busy) "Ищу…" else "Добавить", color = if (busy) Hd.Dim else Hd.Accent)
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss, enabled = !busy) { Text("Отмена", color = Hd.Muted) }
        },
        containerColor = Hd.Surface,
        titleContentColor = Hd.Text,
        textContentColor = Hd.Muted,
    )
}
