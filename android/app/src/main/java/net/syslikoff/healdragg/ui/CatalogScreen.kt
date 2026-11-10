package net.syslikoff.healdragg.ui

import androidx.compose.foundation.background
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
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.GpsFixed
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Public
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.outlined.Explore
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
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
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * База публичных чатов: поиск по названию и @username, листание страниц,
 * переход в чат и «сделать целью» одним тапом.
 */
@Composable
fun CatalogScreen(
    vm: AppViewModel,
    onOpenTarget: (Long) -> Unit,
    onToast: (String) -> Unit,
    onOpenAccounts: () -> Unit,
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val api = vm.api
    val listState = rememberLazyListState()

    var query by rememberSaveable { mutableStateOf("") }
    var rows by remember { mutableStateOf<List<Chat>>(emptyList()) }
    var total by remember { mutableStateOf(0) }
    var page by remember { mutableStateOf(1) }
    var pages by remember { mutableStateOf(1) }
    var loading by remember { mutableStateOf(true) }
    var loadingMore by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var adding by remember { mutableStateOf<Long?>(null) }

    suspend fun load(pageToLoad: Int, reset: Boolean) {
        val client = api ?: return
        if (reset) loading = true else loadingMore = true
        runCatching { client.catalogPage(query.trim().ifBlank { null }, pageToLoad, size = 30) }
            .onSuccess { result ->
                rows = (if (reset) result.rows else rows + result.rows).distinctBy { it.tgId }
                total = result.total
                page = result.page
                pages = result.pages
                error = null
            }
            .onFailure { error = it.message ?: "Не удалось прочитать базу" }
        loading = false
        loadingMore = false
    }

    LaunchedEffect(query) {
        delay(350)
        rows = emptyList()
        page = 1
        pages = 1
        loading = true
        load(1, reset = true)
    }

    LaunchedEffect(vm.bootJob?.finished) {
        if (vm.bootJob?.finished == true) {
            load(1, reset = true)
        }
    }

    LaunchedEffect(listState, page, pages, loadingMore) {
        snapshotFlow { listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0 }
            .collect { last ->
                if (!loadingMore && page < pages && rows.isNotEmpty() && last >= rows.size - 4) {
                    load(page + 1, reset = false)
                }
            }
    }

    fun makeTarget(chat: Chat) {
        val key = chat.username?.takeIf { it.isNotBlank() }?.let { "@$it" } ?: chat.tgId.toString()
        if (adding != null) return
        adding = chat.tgId
        vm.addTarget(key) { result ->
            adding = null
            result.onSuccess { target ->
                onToast("Цель добавлена: ${target.displayName}")
                onOpenTarget(target.tgId)
            }.onFailure { onToast(it.message ?: "Не удалось добавить цель") }
        }
    }

    RefreshableBox(
        refreshing = loading && rows.isNotEmpty(),
        onRefresh = { scope.launch { load(1, reset = true) } },
        modifier = Modifier.fillMaxSize(),
    ) {
        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            state = listState,
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 6.dp, bottom = 96.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item("search") {
                OutlinedTextField(
                    value = query,
                    onValueChange = { query = it },
                    placeholder = {
                        Text(
                            "Название чата или @username",
                            color = Hd.Dim,
                            style = MaterialTheme.typography.bodySmall,
                        )
                    },
                    leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null, tint = Hd.Dim) },
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

            if (vm.bootRunning) {
                item("boot") {
                    val job = vm.bootJob
                    HdCard(contentPadding = PaddingValues(14.dp)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            CircularProgressIndicator(
                                color = Hd.Accent,
                                strokeWidth = 2.dp,
                                modifier = Modifier.size(16.dp),
                            )
                            Spacer(Modifier.width(10.dp))
                            Column(Modifier.weight(1f)) {
                                Text(
                                    "Собираю базу чатов",
                                    style = MaterialTheme.typography.titleSmall,
                                    color = Hd.Text,
                                )
                                Text(
                                    buildString {
                                        append(job?.phase ?: "старт")
                                        job?.let {
                                            if (it.total > 0) append(" · ${it.done}/${it.total}")
                                            if (it.found > 0) append(" · найдено ${it.found}")
                                        }
                                    },
                                    style = MaterialTheme.typography.labelSmall,
                                    color = Hd.Muted,
                                )
                            }
                            TextButton(onClick = { vm.cancelBootstrap() }) {
                                Text("Стоп", color = Hd.Bad)
                            }
                        }
                        if ((job?.total ?: 0) > 0) {
                            Spacer(Modifier.height(8.dp))
                            LinearProgressIndicator(
                                progress = { job?.percent ?: 0f },
                                color = Hd.Accent,
                                trackColor = Hd.Stroke,
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                    }
                }
            }

            error?.let { message ->
                item("error") { ErrorBanner(message = message, onRetry = { scope.launch { load(1, reset = true) } }) }
            }

            if (!vm.bootRunning) {
                vm.bootError?.let { message ->
                    item("boot-error") { ErrorBanner(message = message) }
                }
            }

            if (!loading && rows.isNotEmpty()) {
                item("count") {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        SectionLabel(if (query.isBlank()) "В базе" else "Найдено")
                        Spacer(Modifier.width(8.dp))
                        Text(
                            "$total · стр. $page из $pages",
                            style = MaterialTheme.typography.labelMedium,
                            color = Hd.Dim,
                        )
                    }
                }
            }

            if (loading && rows.isEmpty()) {
                item("loading") { LoadingBlock() }
            }

            if (!loading && rows.isEmpty()) {
                item("empty") {
                    if (query.isBlank()) {
                        EmptyState(
                            emoji = "",
                            icon = Icons.Outlined.Explore,
                            title = "База пока пуста",
                            text = if (vm.hasSessions) {
                                "Собери базу: приложение обойдёт диалоги аккаунта и найдёт публичные чаты. " +
                                    "После этого поиск и скан цели работают точнее."
                            } else {
                                "Сначала подключи Telegram-аккаунт, потом собери базу публичных чатов."
                            },
                            actionLabel = if (vm.hasSessions) {
                                if (vm.bootRunning) null else "Собрать базу"
                            } else {
                                "Войти в Telegram"
                            },
                            onAction = {
                                if (vm.hasSessions) {
                                    vm.startBootstrap()
                                    onToast("Сбор запущен — прогресс видно здесь и в настройках")
                                } else {
                                    onOpenAccounts()
                                }
                            },
                        )
                    } else {
                        EmptyState(
                            emoji = "",
                            icon = Icons.Filled.Search,
                            title = "Ничего не найдено",
                            text = "Попробуй другое слово, @username или часть названия. " +
                                "Если чата нет в базе — сначала собери её.",
                        )
                    }
                }
            }

            items(rows, key = { it.tgId }) { chat ->
                CatalogCard(
                    chat = chat,
                    adding = adding == chat.tgId,
                    onOpen = {
                        if (!openLink(context, chat.link)) {
                            copyText(context, "chat", chat.username?.let { "@$it" } ?: chat.tgId.toString())
                            onToast("Ссылки нет — @username скопирован")
                        }
                    },
                    onCopy = {
                        val text = chat.link ?: chat.username?.let { "https://t.me/$it" }
                        if (text != null) {
                            copyText(context, "chat", text)
                            onToast("Ссылка скопирована")
                        } else {
                            copyText(context, "chat", chat.tgId.toString())
                            onToast("ID скопирован")
                        }
                    },
                    onTarget = { makeTarget(chat) },
                )
            }

            if (loadingMore) {
                item("more") {
                    Box(Modifier.fillMaxWidth().padding(vertical = 10.dp), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator(
                            color = Hd.Accent,
                            strokeWidth = 2.dp,
                            modifier = Modifier.size(18.dp),
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun CatalogCard(
    chat: Chat,
    adding: Boolean,
    onOpen: () -> Unit,
    onCopy: () -> Unit,
    onTarget: () -> Unit,
) {
    HdCard(onClick = onOpen, onLongClick = onCopy, contentPadding = PaddingValues(14.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier
                    .size(40.dp)
                    .background(Hd.avatarColor(chat.tgId).copy(alpha = 0.16f), RoundedCornerShape(12.dp)),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    imageVector = if (chat.isPublic) Icons.Filled.Public else Icons.Filled.Lock,
                    contentDescription = null,
                    tint = Hd.avatarColor(chat.tgId),
                    modifier = Modifier.size(18.dp),
                )
            }
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
                    Text(
                        chat.username?.let { "@$it" } ?: "ID ${chat.tgId}",
                        style = MaterialTheme.typography.labelSmall,
                        color = if (chat.username != null) Hd.Accent2 else Hd.Dim,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    if (chat.members > 0) {
                        Text(
                            " · ${membersLabel(chat.members)}",
                            style = MaterialTheme.typography.labelSmall,
                            color = Hd.Dim,
                            fontFamily = FontFamily.Monospace,
                        )
                    }
                }
            }
            if (adding) {
                CircularProgressIndicator(
                    color = Hd.Accent,
                    strokeWidth = 2.dp,
                    modifier = Modifier.size(18.dp),
                )
            } else {
                IconButton(
                    onClick = onTarget,
                    modifier = Modifier.semantics { contentDescription = "Сделать целью" },
                ) {
                    Icon(Icons.Filled.GpsFixed, contentDescription = null, tint = Hd.Accent)
                }
            }
        }
    }
}
