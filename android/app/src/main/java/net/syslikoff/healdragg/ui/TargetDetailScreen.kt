package net.syslikoff.healdragg.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
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
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private enum class DetailTab(val title: String) {
    Chats("Чаты"),
    Feed("Лента"),
    Network("Сеть"),
}

@Composable
fun TargetDetailScreen(
    vm: AppViewModel,
    tgId: Long,
    onBack: () -> Unit,
    onToast: (String) -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val api = vm.api

    var target by remember(tgId) { mutableStateOf(vm.targetByTgId(tgId)) }
    var error by remember(tgId) { mutableStateOf<String?>(null) }
    var tab by rememberSaveable(tgId) { mutableStateOf(DetailTab.Chats) }
    var menu by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf(false) }

    var chats by remember(tgId) { mutableStateOf<List<Chat>>(emptyList()) }
    var chatOrder by rememberSaveable(tgId) { mutableStateOf("last") }
    var chatsLoading by remember(tgId) { mutableStateOf(false) }
    var chatPage by remember(tgId) { mutableStateOf(1) }
    var chatsDone by remember(tgId) { mutableStateOf(false) }

    var messages by remember(tgId) { mutableStateOf<List<Message>>(emptyList()) }
    var feedLoading by remember(tgId) { mutableStateOf(false) }

    var job by remember(tgId) { mutableStateOf<Job?>(null) }
    var actionBusy by remember { mutableStateOf(false) }
    var linksOpen by remember { mutableStateOf(false) }
    var chatQuery by remember(tgId) { mutableStateOf("") }

    suspend fun loadTarget() {
        val client = api ?: return
        runCatching { client.target(tgId) }
            .onSuccess {
                target = it
                error = null
            }
            .onFailure { error = it.message }
    }

    suspend fun loadChats(reset: Boolean) {
        val client = api ?: return
        if (chatsLoading) return
        chatsLoading = true
        val page = if (reset) 1 else chatPage + 1
        runCatching { client.chats(tgId, page, size = 25, order = chatOrder) }
            .onSuccess { rows ->
                chats = if (reset) rows else chats + rows
                chatPage = page
                chatsDone = rows.size < 25
                error = null
            }
            .onFailure { error = it.message }
        chatsLoading = false
    }

    suspend fun loadFeed() {
        val client = api ?: return
        feedLoading = true
        runCatching { client.activity(tgId, limit = 120) }
            .onSuccess { (_, rows) ->
                messages = rows
                error = null
            }
            .onFailure { error = it.message }
        feedLoading = false
    }

    LaunchedEffect(tgId) { loadTarget() }
    LaunchedEffect(tgId, chatOrder) { loadChats(reset = true) }
    LaunchedEffect(tgId) { loadFeed() }

    LaunchedEffect(job?.key) {
        val key = job?.key ?: return@LaunchedEffect
        val client = api ?: return@LaunchedEffect
        while (true) {
            delay(2_000)
            val fresh = runCatching { client.jobs().firstOrNull { it.key == key } }.getOrNull()
            if (fresh == null) {
                job = null
                break
            }
            job = fresh
            if (fresh.finished) {
                if (fresh.error != null) onToast("Ошибка: ${fresh.error}")
                else onToast("Готово: ${fresh.found} находок")
                loadTarget()
                loadChats(reset = true)
                loadFeed()
                delay(1_200)
                job = null
                break
            }
        }
    }

    fun scan() {
        actionBusy = true
        vm.startScan(tgId, deep = false) { result ->
            actionBusy = false
            result.onSuccess {
                job = it
                onToast("Скан запущен")
            }.onFailure { onToast(it.message ?: "Не удалось запустить скан") }
        }
    }

    fun collect() {
        actionBusy = true
        vm.startCollect(tgId) { result ->
            actionBusy = false
            result.onSuccess {
                job = it
                onToast("Собираю ленту…")
            }.onFailure { onToast(it.message ?: "Не удалось запустить сбор") }
        }
    }

    val listState = rememberLazyListState()
    LaunchedEffect(listState, chatsDone, tab) {
        snapshotFlow { listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0 }
            .collect { last ->
                if (tab == DetailTab.Chats && !chatsDone && !chatsLoading &&
                    last >= chats.size - 3 && chats.isNotEmpty()
                ) {
                    loadChats(reset = false)
                }
            }
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        state = listState,
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 28.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item("header") {
            val data = target
            HdCard(contentPadding = PaddingValues(14.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    IconButton(onClick = onBack) {
                        Icon(Icons.Filled.ArrowBack, contentDescription = "Назад", tint = Hd.Muted)
                    }
                    if (data != null) {
                        Avatar(data.initials, data.tgId, size = 44)
                        Spacer(Modifier.width(10.dp))
                        Column(Modifier.weight(1f)) {
                            Text(
                                data.displayName,
                                style = MaterialTheme.typography.titleMedium,
                                color = Hd.Text,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                            Text(
                                "${data.handle} · ${data.tgId}",
                                style = MaterialTheme.typography.labelMedium,
                                color = Hd.Dim,
                                maxLines = 1,
                            )
                        }
                    } else {
                        Text("Загрузка…", color = Hd.Muted, modifier = Modifier.weight(1f))
                    }
                    Box {
                        IconButton(onClick = { menu = true }) {
                            Icon(Icons.Filled.MoreVert, contentDescription = "Ещё", tint = Hd.Muted)
                        }
                        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                            DropdownMenuItem(
                                text = { Text("Скопировать @username", color = Hd.Text) },
                                onClick = {
                                    menu = false
                                    val handle = target?.username
                                    if (handle != null) {
                                        copyText(context, "username", "@$handle")
                                        onToast("Скопировано")
                                    } else onToast("У цели нет публичного @username")
                                },
                            )
                            DropdownMenuItem(
                                text = { Text("Открыть в Telegram", color = Hd.Text) },
                                onClick = {
                                    menu = false
                                    val link = telegramLink(target?.username)
                                    if (!openLink(context, link)) onToast("Нет ссылки Telegram")
                                },
                            )
                            DropdownMenuItem(
                                text = { Text("Ссылки на все чаты", color = Hd.Text) },
                                onClick = {
                                    menu = false
                                    linksOpen = true
                                },
                            )
                            DropdownMenuItem(
                                text = { Text("Глубокий скан", color = Hd.Text) },
                                onClick = {
                                    menu = false
                                    actionBusy = true
                                    vm.startScan(tgId, deep = true) { result ->
                                        actionBusy = false
                                        result.onSuccess {
                                            job = it
                                            onToast("Глубокий скан запущен")
                                        }.onFailure { onToast(it.message ?: "Не удалось запустить скан") }
                                    }
                                },
                            )
                            DropdownMenuItem(
                                text = { Text("Удалить цель", color = Hd.Bad) },
                                leadingIcon = { Icon(Icons.Filled.Delete, contentDescription = null, tint = Hd.Bad) },
                                onClick = {
                                    menu = false
                                    confirmDelete = true
                                },
                            )
                        }
                    }
                }
                data?.let { t ->
                    Spacer(Modifier.height(10.dp))
                    Row(
                        Modifier.horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Chip("💬 ${t.groupsCount}", Hd.Accent2)
                        Chip("✉️ ${compactNumber(t.activity.messages)}", Hd.Muted)
                        Chip("👥 ${compactNumber(t.activity.peers)}", Hd.Muted)
                        if (t.alertsUnread > 0) Chip("🔔 ${t.alertsUnread}", Hd.Bad)
                        Chip(t.lastScan ?: "не сканировалась", if (t.lastScanAt > 0) Hd.Good else Hd.Dim)
                    }
                }
            }
        }

        item("actions") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = { scan() },
                    enabled = !actionBusy && job == null,
                    shape = RoundedCornerShape(12.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Hd.Accent, contentColor = Hd.Black),
                    modifier = Modifier.weight(1f).heightIn(min = 46.dp),
                ) {
                    Icon(Icons.Filled.PlayArrow, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("Сканировать")
                }
                OutlinedButton(
                    onClick = { collect() },
                    enabled = !actionBusy && job == null,
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.weight(1f).heightIn(min = 46.dp),
                ) {
                    Text("Собрать ленту", color = Hd.Accent2, maxLines = 1, fontSize = 12.sp)
                }
                IconButton(onClick = { linksOpen = true }) {
                    Text("🔗", fontSize = 18.sp)
                }
                IconButton(onClick = { scope.launch { loadTarget(); loadChats(reset = true); loadFeed() } }) {
                    Icon(Icons.Filled.Refresh, contentDescription = "Обновить цель", tint = Hd.Muted)
                }
            }
        }

        job?.let { current ->
            item("job") {
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
                                if (current.kind == "scan") "Сканирую чаты" else "Собираю ленту",
                                style = MaterialTheme.typography.titleSmall,
                                color = Hd.Text,
                            )
                            Text(
                                buildString {
                                    append(current.phase)
                                    if (current.total > 0) append(" · ${current.done}/${current.total}")
                                    if (current.found > 0) append(" · найдено ${current.found}")
                                },
                                style = MaterialTheme.typography.labelSmall,
                                color = Hd.Muted,
                            )
                        }
                        TextButton(onClick = {
                            scope.launch {
                                api?.cancelJob(current.key)
                                onToast("Отменено")
                            }
                        }) { Text("Отменить", color = Hd.Bad) }
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
        }

        error?.let { message ->
            item("error") { ErrorBanner(message, onRetry = { scope.launch { loadTarget(); loadChats(true); loadFeed() } }) }
        }

        item("tabs") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                DetailTab.entries.forEach { item ->
                    FilterChip(
                        selected = tab == item,
                        onClick = { tab = item },
                        label = { Text(item.title) },
                        colors = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = Hd.Accent.copy(alpha = 0.18f),
                            selectedLabelColor = Hd.Accent,
                            labelColor = Hd.Muted,
                            containerColor = Hd.Surface,
                        ),
                    )
                }
            }
        }

        when (tab) {
            DetailTab.Chats -> {
                item("chat-filter") {
                    HdTextField(
                        value = chatQuery,
                        onValueChange = { chatQuery = it },
                        label = "Поиск по чатам",
                        singleLine = true,
                    )
                }
                item("orders") {
                    Row(
                        Modifier.horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        listOf(
                            "last" to "последние",
                            "members" to "крупные",
                            "title" to "алфавит",
                            "found" to "найденные",
                        ).forEach { (key, label) ->
                            FilterChip(
                                selected = chatOrder == key,
                                onClick = { chatOrder = key },
                                label = { Text(label, fontSize = 12.sp) },
                                colors = FilterChipDefaults.filterChipColors(
                                    selectedContainerColor = Hd.Accent2.copy(alpha = 0.16f),
                                    selectedLabelColor = Hd.Accent2,
                                    labelColor = Hd.Dim,
                                    containerColor = Color.Transparent,
                                ),
                            )
                        }
                    }
                }
                if (chats.isEmpty() && !chatsLoading) {
                    item("chats-empty") {
                        EmptyState(
                            emoji = "💬",
                            title = "Чатов пока нет",
                            text = "Нажми «Сканировать» — приложение проверит, в каких публичных чатах состоит цель.",
                        )
                    }
                }
                items(
                    if (chatQuery.isBlank()) chats else chats.filter {
                        it.title.contains(chatQuery, ignoreCase = true) ||
                            (it.username?.contains(chatQuery, ignoreCase = true) == true)
                    },
                    key = { it.tgId },
                ) { chat ->
                    ChatCard(chat = chat, onToast = onToast)
                }
                if (chatsLoading) {
                    item("chats-loading") { LoadingBlock() }
                }
            }

            DetailTab.Feed -> {
                if (messages.isEmpty() && !feedLoading) {
                    item("feed-empty") {
                        EmptyState(
                            emoji = "📜",
                            title = "Лента пуста",
                            text = "Нажми «Собрать ленту» — приложение вытащит последние сообщения цели из её чатов.",
                            actionLabel = "Собрать ленту",
                            onAction = { collect() },
                        )
                    }
                }
                items(messages, key = { it.id }) { message ->
                    MessageCard(message, onToast)
                }
                if (feedLoading) item("feed-loading") { LoadingBlock() }
            }

            DetailTab.Network -> item("graph") {
                GraphTab(api = api, tgId = tgId, onToast = onToast)
            }
        }
    }

    if (linksOpen) {
        LinksDialog(
            api = api,
            tgId = tgId,
            onDismiss = { linksOpen = false },
            onToast = onToast,
        )
    }

    if (confirmDelete) {
        AlertDialog(
            onDismissRequest = { confirmDelete = false },
            title = { Text("Удалить цель?") },
            text = {
                Text(
                    "«${target?.displayName ?: tgId}» и все её чаты, сообщения и алерты исчезнут из базы.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Hd.Muted,
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    confirmDelete = false
                    vm.deleteTarget(tgId) { ok ->
                        if (ok) {
                            onToast("Удалено")
                            onBack()
                        } else onToast("Не удалось удалить")
                    }
                }) { Text("Удалить", color = Hd.Bad) }
            },
            dismissButton = { TextButton(onClick = { confirmDelete = false }) { Text("Отмена", color = Hd.Muted) } },
            containerColor = Hd.Surface,
            titleContentColor = Hd.Text,
            textContentColor = Hd.Muted,
        )
    }
}

@Composable
private fun ChatCard(chat: Chat, onToast: (String) -> Unit) {
    val context = LocalContext.current
    HdCard(onClick = {
        if (!openLink(context, chat.link)) {
            onToast("У чата нет публичной ссылки")
        }
    }, contentPadding = PaddingValues(14.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier
                    .size(40.dp)
                    .background(Hd.avatarColor(chat.tgId).copy(alpha = 0.16f), RoundedCornerShape(12.dp)),
                contentAlignment = Alignment.Center,
            ) { Text(if (chat.isPublic) "🌐" else "🔒", fontSize = 16.sp) }
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    chat.title,
                    style = MaterialTheme.typography.titleSmall,
                    color = Hd.Text,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    if (chat.username != null) {
                        Text(
                            "@${chat.username}",
                            style = MaterialTheme.typography.labelSmall,
                            color = Hd.Accent2,
                            maxLines = 1,
                        )
                        if (chat.members > 0) Text(" · ", style = MaterialTheme.typography.labelSmall, color = Hd.Dim)
                    }
                    if (chat.members > 0) {
                        Text(
                            membersLabel(chat.members),
                            style = MaterialTheme.typography.labelSmall,
                            color = Hd.Dim,
                        )
                    }
                }
            }
            Column(horizontalAlignment = Alignment.End) {
                StatusPill(chat.status)
                if (chat.lastMessageAt > 0) {
                    Spacer(Modifier.height(4.dp))
                    Text(
                        relativeTime(chat.lastMessageAt),
                        style = MaterialTheme.typography.labelSmall,
                        color = Hd.Dim,
                        fontFamily = FontFamily.Monospace,
                    )
                }
            }
        }
    }
}

@Composable
private fun MessageCard(message: Message, onToast: (String) -> Unit) {
    val context = LocalContext.current
    HdCard(contentPadding = PaddingValues(14.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                message.groupTitle,
                style = MaterialTheme.typography.labelMedium,
                color = Hd.Accent2,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f).clickable {
                    if (!openLink(context, message.groupLink)) onToast("Нет ссылки на чат")
                },
            )
            Spacer(Modifier.width(6.dp))
            Text(
                relativeTime(message.date),
                style = MaterialTheme.typography.labelSmall,
                color = Hd.Dim,
                fontFamily = FontFamily.Monospace,
            )
        }
        if (message.text.isNotBlank()) {
            Spacer(Modifier.height(6.dp))
            Text(
                message.text,
                style = MaterialTheme.typography.bodyMedium,
                color = Hd.Text,
                maxLines = 12,
                overflow = TextOverflow.Ellipsis,
            )
        } else if (message.media != null) {
            Spacer(Modifier.height(6.dp))
            Text("вложение: ${message.media}", style = MaterialTheme.typography.bodySmall, color = Hd.Muted)
        }
        val meta = buildList {
            message.media?.let { add("📎 $it") }
            if (message.views > 0) add("👁 ${compactNumber(message.views)}")
            if (message.replies) add("↩ ответ")
        }
        if (meta.isNotEmpty()) {
            Spacer(Modifier.height(6.dp))
            Text(
                meta.joinToString("  ·  "),
                style = MaterialTheme.typography.labelSmall,
                color = Hd.Dim,
            )
        }
    }
}


@Composable
private fun LinksDialog(api: Api?, tgId: Long, onDismiss: () -> Unit, onToast: (String) -> Unit) {
    val context = LocalContext.current
    var rows by remember { mutableStateOf<List<ChatRow>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }

    LaunchedEffect(tgId) {
        rows = api?.links(tgId, offset = 0, limit = 500)?.rows.orEmpty()
        loading = false
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Ссылки на чаты · ${rows.size}") },
        text = {
            if (loading) {
                LoadingBlock()
            } else if (rows.isEmpty()) {
                Text("Ссылок пока нет — сначала просканируй цель.", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
            } else {
                LazyColumn(Modifier.heightIn(max = 430.dp)) {
                    items(rows, key = { it.tgId }) { row ->
                        Row(
                            Modifier.fillMaxWidth().padding(vertical = 6.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(
                                    row.title,
                                    style = MaterialTheme.typography.titleSmall,
                                    color = Hd.Text,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                                Text(
                                    row.username?.let { "@$it" } ?: "приватный · ID ${row.tgId}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = Hd.Dim,
                                    maxLines = 1,
                                )
                            }
                            TextButton(onClick = {
                                if (row.link != null) {
                                    copyText(context, "link", row.link!!)
                                    onToast("Ссылка скопирована")
                                } else onToast("У чата нет ссылки")
                            }) { Text("⧉", color = Hd.Accent2, fontSize = 16.sp) }
                            TextButton(onClick = {
                                if (!openLink(context, row.link)) onToast("Нет ссылки")
                            }) { Text("↗", color = Hd.Muted, fontSize = 16.sp) }
                        }
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Закрыть", color = Hd.Accent) } },
        containerColor = Hd.Surface,
        titleContentColor = Hd.Text,
    )
}
