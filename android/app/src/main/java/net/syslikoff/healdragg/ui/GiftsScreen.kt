package net.syslikoff.healdragg.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.outlined.CardGiftcard
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Подарки Telegram: кто, кому, что и когда дарил. Обход идёт по профилям:
 * цель → её дарители → их дарители, с ограничением числа профилей.
 */
@Composable
fun GiftsScreen(vm: AppViewModel, onToast: (String) -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val api = vm.api

    var query by rememberSaveable { mutableStateOf("") }
    var depth by rememberSaveable { mutableStateOf(1) }
    var maxProfiles by rememberSaveable { mutableStateOf(5) }
    var nftOnly by rememberSaveable { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var job by remember { mutableStateOf<Job?>(null) }
    var rows by remember { mutableStateOf<List<GiftItem>>(emptyList()) }
    var summary by remember { mutableStateOf(GiftSummary()) }
    var loading by remember { mutableStateOf(true) }

    suspend fun reload() {
        val client = api ?: run {
            loading = false
            return
        }
        loading = true
        runCatching { client.gifts(nftOnly = nftOnly) }
            .onSuccess {
                rows = it.rows
                summary = it.summary
                error = null
            }
            .onFailure { error = it.message ?: "Не удалось прочитать подарки" }
        loading = false
    }

    LaunchedEffect(nftOnly) { reload() }

    LaunchedEffect(job?.key) {
        val key = job?.key ?: return@LaunchedEffect
        val client = api ?: return@LaunchedEffect
        while (true) {
            delay(2_500)
            val fresh = runCatching { client.jobs().firstOrNull { it.key == key } }.getOrNull()
            if (fresh == null) {
                job = null
                break
            }
            job = fresh
            if (fresh.finished) {
                if (fresh.error != null) {
                    error = fresh.error
                    onToast("Обход прервался: ${fresh.error}")
                } else {
                    onToast("Готово: подарков ${fresh.found}")
                }
                reload()
                delay(1_500)
                job = null
                break
            }
        }
    }

    fun start() {
        val target = query.trim()
        if (target.isEmpty()) {
            onToast("Вставь @username, ссылку t.me/… или ID")
            return
        }
        if (busy || job != null) return
        busy = true
        error = null
        vm.crawlGifts(target, depth, maxProfiles) { result ->
            busy = false
            result.onSuccess {
                job = it
                onToast("Обход запущен")
            }.onFailure { error = it.message ?: "Не удалось запустить обход" }
        }
    }

    RefreshableBox(
        refreshing = loading && rows.isNotEmpty(),
        onRefresh = { scope.launch { reload() } },
        modifier = Modifier.fillMaxSize(),
    ) {
        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 6.dp, bottom = 96.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item("form") {
                HdCard {
                    Text(
                        "Подарки и дарители",
                        style = MaterialTheme.typography.titleSmall,
                        color = Hd.Text,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "Вставь @username, ссылку t.me/… или ID профиля. Приложение соберёт, " +
                            "кто кому и когда дарил подарки — с текстом и датой.",
                        style = MaterialTheme.typography.bodySmall,
                        color = Hd.Muted,
                    )
                    Spacer(Modifier.height(10.dp))
                    HdTextField(
                        value = query,
                        onValueChange = { query = it },
                        label = "Профиль",
                        hint = "@durov или 777000",
                        singleLine = true,
                    )
                    Spacer(Modifier.height(10.dp))
                    Text("Глубина", style = MaterialTheme.typography.labelMedium, color = Hd.Dim)
                    Row(
                        Modifier.fillMaxWidth().padding(top = 4.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        listOf(
                            1 to "профиль",
                            2 to "друзья",
                            3 to "глубже",
                        ).forEach { (value, label) ->
                            FilterChip(
                                selected = depth == value,
                                onClick = { depth = value },
                                label = { Text(label) },
                                colors = giftChipColors(),
                            )
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Сколько профилей пройти",
                        style = MaterialTheme.typography.labelMedium,
                        color = Hd.Dim,
                    )
                    Row(
                        Modifier.fillMaxWidth().padding(top = 4.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        listOf(5, 10, 25).forEach { value ->
                            FilterChip(
                                selected = maxProfiles == value,
                                onClick = { maxProfiles = value },
                                label = { Text("до $value") },
                                colors = giftChipColors(),
                            )
                        }
                    }
                    Spacer(Modifier.height(10.dp))
                    Button(
                        onClick = { start() },
                        enabled = !busy && job == null && query.isNotBlank(),
                        shape = RoundedCornerShape(12.dp),
                        colors = ButtonDefaults.buttonColors(
                            containerColor = Hd.Accent,
                            contentColor = Hd.Black,
                        ),
                        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                    ) {
                        if (busy || job != null) {
                            CircularProgressIndicator(
                                strokeWidth = 2.dp,
                                color = Hd.Black,
                                modifier = Modifier.size(16.dp),
                            )
                            Spacer(Modifier.width(8.dp))
                        }
                        Text(if (job != null) "Обход идёт…" else "Собрать подарки")
                    }
                    if (depth > 1) {
                        Spacer(Modifier.height(6.dp))
                        Text(
                            "При глубине $depth приложение зайдёт в профили тех, кто дарил подарки, " +
                                "и соберёт подарки уже у них.",
                            style = MaterialTheme.typography.labelSmall,
                            color = Hd.Dim,
                        )
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
                                    "Читаю подарки",
                                    style = MaterialTheme.typography.titleSmall,
                                    color = Hd.Text,
                                )
                                Text(
                                    buildString {
                                        append(current.phase)
                                        if (current.total > 0) append(" · ${current.done}/${current.total}")
                                        if (current.found > 0) append(" · подарков ${current.found}")
                                    },
                                    style = MaterialTheme.typography.labelSmall,
                                    color = Hd.Muted,
                                )
                            }
                            TextButton(onClick = {
                                scope.launch {
                                    api?.cancelJob(current.key)
                                    job = null
                                    onToast("Обход отменён")
                                }
                            }) { Text("Стоп", color = Hd.Bad) }
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
                item("error") {
                    ErrorBanner(message = message, onRetry = { scope.launch { reload() } })
                }
            }

            item("summary") {
                Row(
                    Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    StatTile("${summary.total}", "подарков", Hd.Accent2)
                    StatTile("${summary.nft}", "NFT", Hd.Accent)
                    StatTile("${summary.owners}", "профилей", Hd.Good)
                    StatTile("${summary.senders}", "дарителей", Hd.Text)
                }
            }

            item("filter") {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(
                        selected = !nftOnly,
                        onClick = { nftOnly = false },
                        label = { Text("Все · ${summary.total}") },
                        colors = giftChipColors(),
                    )
                    FilterChip(
                        selected = nftOnly,
                        onClick = { nftOnly = true },
                        label = { Text("NFT · ${summary.nft}") },
                        colors = giftChipColors(),
                    )
                }
            }

            if (loading && rows.isEmpty()) {
                item("loading") { LoadingBlock() }
            }

            if (!loading && rows.isEmpty()) {
                item("empty") {
                    if (nftOnly && summary.total > 0) {
                        EmptyState(
                            emoji = "",
                            icon = Icons.Filled.AutoAwesome,
                            title = "NFT пока не найдено",
                            text = "Собери подарки по профилю, где они точно есть, или увеличь глубину обхода.",
                        )
                    } else {
                        EmptyState(
                            emoji = "",
                            icon = Icons.Outlined.CardGiftcard,
                            title = "Подарков пока нет",
                            text = "Вставь профиль в поле выше и запусти сбор: приложение покажет, " +
                                "кто, кому, что и когда дарил — включая NFT.",
                        )
                    }
                }
            }

            items(rows, key = { it.id }) { gift ->
                GiftCard(gift = gift, onToast = onToast)
            }
        }
    }
}

@Composable
private fun giftChipColors() = FilterChipDefaults.filterChipColors(
    selectedContainerColor = Hd.Accent.copy(alpha = 0.18f),
    selectedLabelColor = Hd.Accent,
    labelColor = Hd.Muted,
    containerColor = Hd.Surface,
)

@Composable
private fun GiftCard(gift: GiftItem, onToast: (String) -> Unit) {
    val context = LocalContext.current
    val ownerLink = gift.ownerUsername?.let { "https://t.me/$it" }
    HdCard(
        onClick = {
            if (!openLink(context, ownerLink)) {
                copyText(context, "owner", gift.ownerHandle)
                onToast("Ссылки нет — профиль скопирован")
            }
        },
        onLongClick = {
            val payload = buildString {
                append(gift.title)
                gift.message?.let { append(" · «$it»") }
                append(" · ${gift.ownerHandle}")
                if (gift.fromTgId > 0) {
                    append(" ← ")
                    append(gift.fromUsername?.let { "@$it" } ?: gift.fromName ?: "id ${gift.fromTgId}")
                }
            }
            copyText(context, "gift", payload)
            onToast("Подарок скопирован")
        },
        contentPadding = PaddingValues(14.dp),
    ) {
        Row(verticalAlignment = Alignment.Top) {
            Box(
                Modifier
                    .size(40.dp)
                    .background(
                        if (gift.isNft) Hd.Accent.copy(alpha = 0.16f) else Hd.Accent2.copy(alpha = 0.14f),
                        RoundedCornerShape(12.dp),
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    imageVector = if (gift.isNft) Icons.Filled.AutoAwesome else Icons.Outlined.CardGiftcard,
                    contentDescription = null,
                    tint = if (gift.isNft) Hd.Accent else Hd.Accent2,
                    modifier = Modifier.size(20.dp),
                )
            }
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        gift.title,
                        style = MaterialTheme.typography.titleSmall,
                        color = Hd.Text,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                    if (gift.isNft) {
                        Spacer(Modifier.width(6.dp))
                        Text(
                            "NFT",
                            style = MaterialTheme.typography.labelSmall,
                            color = Hd.Accent,
                            modifier = Modifier
                                .background(Hd.Accent.copy(alpha = 0.16f), RoundedCornerShape(6.dp))
                                .padding(horizontal = 5.dp, vertical = 1.dp),
                        )
                    }
                }
                Spacer(Modifier.height(3.dp))
                Text(
                    "кому: ${gift.ownerName ?: gift.ownerHandle}",
                    style = MaterialTheme.typography.labelSmall,
                    color = Hd.Muted,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    buildString {
                        append("от: ")
                        append(
                            when {
                                gift.nameHidden -> "аноним"
                                gift.fromTgId > 0 -> gift.fromName
                                    ?: gift.fromUsername?.let { "@$it" }
                                    ?: "id ${gift.fromTgId}"
                                else -> "неизвестно"
                            },
                        )
                        if (gift.stars > 0) append(" · ${gift.stars}★")
                    },
                    style = MaterialTheme.typography.labelSmall,
                    color = Hd.Dim,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                gift.message?.takeIf { it.isNotBlank() }?.let { message ->
                    Spacer(Modifier.height(5.dp))
                    Text(
                        "«$message»",
                        style = MaterialTheme.typography.bodySmall,
                        color = Hd.Text,
                        maxLines = 3,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            Spacer(Modifier.width(6.dp))
            Column(horizontalAlignment = Alignment.End) {
                Text(
                    relativeTime(gift.date),
                    style = MaterialTheme.typography.labelSmall,
                    color = Hd.Dim,
                    fontFamily = FontFamily.Monospace,
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    if (gift.kind == "unique") "уник." else "обыч.",
                    style = MaterialTheme.typography.labelSmall,
                    color = Hd.Dim,
                )
            }
        }
    }
}
