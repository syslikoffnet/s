package net.syslikoff.healdragg.ui

import android.graphics.BitmapFactory
import android.util.Base64
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.verticalScroll
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
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.outlined.CardGiftcard
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Подарки Telegram: кто, кому, что и когда дарил. Обход идёт по профилям:
 * цель → её дарители → их дарители, с ограничением числа профилей.
 */
@Composable
fun GiftsScreen(
    vm: AppViewModel,
    onToast: (String) -> Unit,
    onOpenProfile: (String) -> Unit = {},
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val api = vm.api
    var selectedOwner by remember { mutableStateOf<NftOwner?>(null) }
    selectedOwner?.let { owner ->
        BackHandler { selectedOwner = null }
        OwnerGiftsScreen(api, owner, onBack = { selectedOwner = null }, onToast = onToast, onOpenProfile = onOpenProfile)
        return
    }

    var query by rememberSaveable { mutableStateOf("") }
    var depth by rememberSaveable { mutableStateOf(8) }
    var maxProfiles by rememberSaveable { mutableStateOf(5000) }
    var nftOnly by rememberSaveable { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var job by remember { mutableStateOf<Job?>(null) }
    var rows by remember { mutableStateOf<List<GiftItem>>(emptyList()) }
    var summary by remember { mutableStateOf(GiftSummary()) }
    var loading by remember { mutableStateOf(true) }
    var mode by rememberSaveable { mutableStateOf(0) }
    var chain by rememberSaveable { mutableStateOf("") }
    var chainRequests by rememberSaveable { mutableStateOf(2_000) }
    var profileBudget by rememberSaveable { mutableStateOf(200) }
    var nftRows by remember { mutableStateOf<List<NftItem>>(emptyList()) }
    var nftSummary by remember { mutableStateOf(NftSummary()) }
    var nftOwnersList by remember { mutableStateOf<List<NftOwner>>(emptyList()) }
    var collections by remember { mutableStateOf<List<NftCollection>>(emptyList()) }
    var nftQuery by rememberSaveable { mutableStateOf("") }
    var nftSort by rememberSaveable { mutableStateOf("value") }
    var nftHasMore by remember { mutableStateOf(false) }
    var nftResale by rememberSaveable { mutableStateOf(false) }
    var nftCollection by rememberSaveable { mutableStateOf("") }
    var nftTotal by remember { mutableStateOf(0) }
    var ownerQuery by rememberSaveable { mutableStateOf("") }
    var ownerTotal by remember { mutableStateOf(0) }
    var nftMoreBusy by remember { mutableStateOf(false) }
    var pendingCsv by remember { mutableStateOf<ByteArray?>(null) }
    val saveCsv = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("text/csv"),
    ) { uri ->
        val payload = pendingCsv
        pendingCsv = null
        if (uri == null || payload == null) return@rememberLauncherForActivityResult
        val ok = writeDocument(context, uri, payload)
        onToast(if (ok) "CSV сохранён" else "Не удалось записать CSV")
    }

    suspend fun reload() {
        val client = api ?: run {
            loading = false
            return
        }
        loading = true
        val chainArg = chain.ifBlank { null }
        runCatching { client.gifts(nftOnly = nftOnly, chain = chainArg) }
            .onSuccess {
                rows = it.rows
                summary = it.summary
                error = null
            }
            .onFailure { error = it.message ?: "Не удалось прочитать подарки" }
        if (mode == 2) {
            runCatching {
                client.giftOwners(
                    q = ownerQuery.trim().ifBlank { null },
                    limit = 500,
                )
            }
                .onSuccess {
                    nftOwnersList = it
                    ownerTotal = it.size
                }
                .onFailure { error = it.message ?: "Не удалось прочитать владельцев" }
        }
        loading = false
    }

    suspend fun loadNft(reset: Boolean) {
        val client = api ?: return
        val chainArg = chain.ifBlank { null }
        val query = nftQuery.trim().ifBlank { null }
        val offset = if (reset) 0 else nftRows.size
        runCatching {
            client.giftsNft(
                chain = chainArg,
                q = query,
                sort = nftSort,
                limit = 200,
                offset = offset,
                resaleOnly = nftResale,
                collection = nftCollection.ifBlank { null },
            )
        }
            .onSuccess {
                nftSummary = it.summary
                collections = it.collections
                nftRows = if (reset) it.rows else (nftRows + it.rows).distinctBy { row -> row.slug }
                nftTotal = it.total
                nftHasMore = (if (reset) it.rows.size else nftRows.size) < it.total
                error = null
            }
            .onFailure { error = it.message ?: "Не удалось прочитать NFT-инвентарь" }
    }

    fun loadMoreNft() {
        if (nftMoreBusy || !nftHasMore) return
        nftMoreBusy = true
        scope.launch {
            loadNft(reset = false)
            nftMoreBusy = false
        }
    }

    fun exportNft() {
        scope.launch {
            val client = api ?: return@launch
            onToast("Готовлю CSV…")
            runCatching {
                client.nftCsv(
                    chain = chain.ifBlank { null },
                    q = nftQuery.trim().ifBlank { null },
                    sort = nftSort,
                    resaleOnly = nftResale,
                    collection = nftCollection.ifBlank { null },
                )
            }
                .onSuccess { payload ->
                    pendingCsv = payload
                    saveCsv.launch("healdragg-nft.csv")
                }
                .onFailure { onToast(it.message ?: "Не удалось выгрузить CSV") }
        }
    }

    LaunchedEffect(mode, chain, nftOnly) { reload() }
    LaunchedEffect(mode, ownerQuery) {
        if (mode != 2) return@LaunchedEffect
        if (ownerQuery.isNotBlank()) delay(400)
        reload()
    }
    LaunchedEffect(mode, chain, nftQuery, nftSort, nftResale, nftCollection) {
        if (mode == 0) return@LaunchedEffect
        if (nftQuery.isNotBlank()) delay(400)
        loadNft(reset = true)
    }

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
            if (!fresh.finished) {
                // real-time: пока идёт обход, показываем уже найденное
                if (mode != 0) loadNft(reset = true)
                reload()
            }
            if (fresh.finished) {
                if (fresh.error != null) {
                    error = fresh.error
                    onToast("Обход прервался: ${fresh.error}")
                } else {
                    onToast("Готово: подарков ${fresh.found}")
                }
                reload()
                loadNft(reset = true)
                delay(1_500)
                job = null
                break
            }
        }
    }

    fun refreshKnown() {
        if (busy || job != null) return
        busy = true
        error = null
        vm.refreshGifts(limit = 500, perProfile = 200) { result ->
            busy = false
            result.onSuccess {
                job = it
                onToast("Обновляю уже известные профили…")
            }.onFailure { error = it.message ?: "Не удалось запустить обновление" }
        }
    }

    fun startChain() {
        if (busy || job != null) return
        busy = true
        error = null
        vm.startNftChain(chainRequests) { result ->
            busy = false
            result.onSuccess {
                job = it
                mode = 1
                onToast("Обход NFT-коллекций запущен")
            }.onFailure { error = it.message ?: "Не удалось запустить обход NFT" }
        }
    }

    fun startProfiles() {
        if (busy || job != null) return
        busy = true
        error = null
        vm.startNftProfiles(profileBudget) { result ->
            busy = false
            result.onSuccess {
                job = it
                mode = 2
                onToast("Читаю подарки владельцев NFT…")
            }.onFailure { error = it.message ?: "Не удалось запустить обход профилей" }
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
                            "кто кому и когда дарил подарки — с текстом и датой. Изображения NFT " +
                            "загружаются с Fragment и кэшируются на устройстве.",
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
                    Text("Глубина по сети", style = MaterialTheme.typography.labelMedium, color = Hd.Dim)
                    Row(
                        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(top = 4.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        listOf(1, 2, 3, 4, 5, 6, 7, 8).forEach { value ->
                            FilterChip(
                                selected = depth == value,
                                onClick = { depth = value },
                                label = { Text(if (value == 8) "вся" else value.toString()) },
                                colors = giftChipColors(),
                            )
                        }
                    }
                    Spacer(Modifier.height(4.dp))
                    Text(
                        when (depth) {
                            1 -> "1 — только этот профиль"
                            2 -> "2 — профиль и те, кто ему дарил"
                            3 -> "3 — дарители дарителей"
                            8 -> "вся сеть: идём по дарителям, пока хватает лимита профилей"
                            else -> "$depth — на $depth шага по сети дарителей"
                        },
                        style = MaterialTheme.typography.labelSmall,
                        color = Hd.Dim,
                    )
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "Сколько профилей пройти",
                        style = MaterialTheme.typography.labelMedium,
                        color = Hd.Dim,
                    )
                    Row(
                        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(top = 4.dp),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        listOf(5, 50, 250, 1_000, 5_000).forEach { value ->
                            FilterChip(
                                selected = maxProfiles == value,
                                onClick = { maxProfiles = value },
                                label = { Text("до ${compactNumber(value)}") },
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
                    Spacer(Modifier.height(8.dp))
                    OutlinedButton(
                        onClick = { refreshKnown() },
                        enabled = !busy && job == null && summary.total > 0,
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.fillMaxWidth().heightIn(min = 44.dp),
                    ) {
                        Icon(
                            Icons.Filled.Refresh,
                            contentDescription = null,
                            tint = Hd.Accent2,
                            modifier = Modifier.size(18.dp),
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            if (summary.total > 0) "Обновить уже известные (${summary.owners})" else "Пока нечего обновлять",
                            color = Hd.Accent2,
                            style = MaterialTheme.typography.labelLarge,
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
                                    if (current.kind == "giftchain") "Собираю NFT-инвентарь"
                                    else "Читаю подарки",
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

            item("modes") {
                Row(
                    Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    listOf("Подарки", "NFT", "Владельцы").forEachIndexed { index, label ->
                        FilterChip(
                            selected = mode == index,
                            onClick = { mode = index },
                            label = { Text(label) },
                            colors = giftChipColors(),
                        )
                    }
                }
            }

            if (mode != 0) {
                item("chain") {
                    Row(
                        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        listOf(
                            "" to "Все",
                            "wallet" to "На кошельке",
                            "telegram" to "В Telegram",
                        ).forEach { (value, label) ->
                            FilterChip(
                                selected = chain == value,
                                onClick = { chain = value },
                                label = { Text(label) },
                                colors = giftChipColors(),
                            )
                        }
                    }
                }

                item("nftSearch") {
                    HdCard(contentPadding = PaddingValues(12.dp)) {
                        HdTextField(
                            value = nftQuery,
                            onValueChange = { nftQuery = it.take(80) },
                            label = "Поиск по базе NFT",
                            hint = "ник, id, PlushPepe, адрес кошелька",
                            singleLine = true,
                            trailing = if (nftQuery.isNotEmpty()) {
                                {
                                    IconButton(onClick = { nftQuery = "" }) {
                                        Icon(
                                            Icons.Filled.Clear,
                                            contentDescription = "Очистить поиск",
                                            tint = Hd.Muted,
                                            modifier = Modifier.size(18.dp),
                                        )
                                    }
                                }
                            } else {
                                null
                            },
                        )
                        Spacer(Modifier.height(6.dp))
                        Text(
                            if (nftRows.isEmpty()) "В базе пока ничего не найдено"
                            else "Найдено ${compactNumber(nftTotal)} · показано ${compactNumber(nftRows.size)}",
                            style = MaterialTheme.typography.labelSmall,
                            color = Hd.Dim,
                        )
                        if (nftResale || nftCollection.isNotBlank()) {
                            Spacer(Modifier.height(6.dp))
                            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                if (nftResale) {
                                    FilterChip(
                                        selected = true,
                                        onClick = { nftResale = false },
                                        label = { Text("только ресейл") },
                                        trailingIcon = {
                                            Icon(
                                                Icons.Filled.Clear,
                                                contentDescription = null,
                                                tint = Hd.Muted,
                                                modifier = Modifier.size(14.dp),
                                            )
                                        },
                                        colors = giftChipColors(),
                                    )
                                }
                                if (nftCollection.isNotBlank()) {
                                    FilterChip(
                                        selected = true,
                                        onClick = { nftCollection = "" },
                                        label = { Text(nftCollection) },
                                        trailingIcon = {
                                            Icon(
                                                Icons.Filled.Clear,
                                                contentDescription = null,
                                                tint = Hd.Muted,
                                                modifier = Modifier.size(14.dp),
                                            )
                                        },
                                        colors = giftChipColors(),
                                    )
                                }
                            }
                        }
                        Spacer(Modifier.height(8.dp))
                        Row(
                            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            listOf(
                                "value" to "Дороже",
                                "resell" to "Ресейл",
                                "recent" to "Новые",
                                "num" to "По номеру",
                            ).forEach { (value, label) ->
                                FilterChip(
                                    selected = nftSort == value,
                                    onClick = { nftSort = value },
                                    label = { Text(label) },
                                    colors = giftChipColors(),
                                )
                            }
                            FilterChip(
                                selected = nftResale,
                                onClick = { nftResale = !nftResale },
                                label = { Text("На продажу") },
                                leadingIcon = if (nftResale) {
                                    {
                                        Icon(
                                            Icons.Filled.Check,
                                            contentDescription = null,
                                            tint = Hd.Accent,
                                            modifier = Modifier.size(14.dp),
                                        )
                                    }
                                } else {
                                    null
                                },
                                colors = giftChipColors(),
                            )
                        }
                        Spacer(Modifier.height(8.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(
                                onClick = { exportNft() },
                                enabled = nftRows.isNotEmpty(),
                                shape = RoundedCornerShape(12.dp),
                                modifier = Modifier.weight(1f),
                            ) {
                                Text("Выгрузить CSV", color = Hd.Accent2, style = MaterialTheme.typography.labelMedium)
                            }
                            OutlinedButton(
                                onClick = { scope.launch { loadNft(reset = true) } },
                                shape = RoundedCornerShape(12.dp),
                                modifier = Modifier.weight(1f),
                            ) {
                                Text("Обновить", color = Hd.Accent2, style = MaterialTheme.typography.labelMedium)
                            }
                        }
                    }
                }

                item("nftSummary") {
                    Row(
                        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        StatTile("${nftSummary.total}", "NFT в базе", Hd.Accent)
                        StatTile("${nftSummary.onChain}", "на кошельках", Hd.Warn)
                        StatTile("${nftSummary.hosted}", "в Telegram", Hd.Accent2)
                        StatTile("${nftSummary.owners}", "владельцев", Hd.Good)
                    }
                }

                item("chainForm") {
                    HdCard {
                        Text(
                            "Массовый сбор NFT-владельцев",
                            style = MaterialTheme.typography.titleSmall,
                            color = Hd.Text,
                        )
                        Spacer(Modifier.height(4.dp))
                        Text(
                            "Перебираем уникальные подарки по номерам во всех коллекциях и " +
                                "запоминаем владельцев. Прогресс хранится в базе — обход можно " +
                                "останавливать и продолжать, он не начнётся заново.",
                            style = MaterialTheme.typography.bodySmall,
                            color = Hd.Muted,
                        )
                        Spacer(Modifier.height(8.dp))
                        Text(
                            "Сколько запросов за проход",
                            style = MaterialTheme.typography.labelMedium,
                            color = Hd.Dim,
                        )
                        Row(
                            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(top = 4.dp),
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            listOf(500, 2_000, 10_000, 50_000, 200_000).forEach { value ->
                                FilterChip(
                                    selected = chainRequests == value,
                                    onClick = { chainRequests = value },
                                    label = { Text(compactNumber(value)) },
                                    colors = giftChipColors(),
                                )
                            }
                        }
                        Spacer(Modifier.height(4.dp))
                        Text(
                            "Каждый запрос — одна вещь. 50 000 запросов ≈ 15–20 часов " +
                                "спокойного темпа, лимиты Telegram приложение соблюдает само.",
                            style = MaterialTheme.typography.labelSmall,
                            color = Hd.Dim,
                        )
                        Spacer(Modifier.height(10.dp))
                        Button(
                            onClick = { startChain() },
                            enabled = !busy && job == null,
                            shape = RoundedCornerShape(12.dp),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = Hd.Accent,
                                contentColor = Hd.Black,
                            ),
                            modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                        ) {
                            Icon(
                                Icons.Filled.AutoAwesome,
                                contentDescription = null,
                                tint = Hd.Black,
                                modifier = Modifier.size(18.dp),
                            )
                            Spacer(Modifier.width(8.dp))
                            Text("Собрать NFT-инвентарь")
                        }
                        if (collections.isNotEmpty()) {
                            Spacer(Modifier.height(10.dp))
                            val scanned = collections.sumOf { it.scanned }
                            val total = collections.sumOf { it.total }
                            Text(
                                "Коллекции: ${collections.size} · просмотрено " +
                                    "${compactNumber(scanned)} из ${compactNumber(total)}",
                                style = MaterialTheme.typography.labelSmall,
                                color = Hd.Dim,
                            )
                            collections.take(3).forEach { collection ->
                                Spacer(Modifier.height(6.dp))
                                Text(
                                    (collection.title ?: collection.slugBase) +
                                        " · ${compactNumber(collection.scanned)}/" +
                                        compactNumber(collection.total),
                                    style = MaterialTheme.typography.labelSmall,
                                    color = Hd.Muted,
                                )
                                LinearProgressIndicator(
                                    progress = { collection.percent },
                                    color = Hd.Accent,
                                    trackColor = Hd.Stroke,
                                    modifier = Modifier.fillMaxWidth().padding(top = 3.dp),
                                )
                            }
                        }
                    }
                }
            }

            if (mode == 0) {
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
                                text = "Собери подарки по профилю, где они точно есть, или запусти " +
                                    "массовый обход NFT-коллекций во вкладке NFT.",
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
                    GiftCard(gift = gift, api = api, onToast = onToast, onOpenProfile = onOpenProfile)
                }
            }

            if (mode == 1) {
                if (loading && nftRows.isEmpty()) {
                    item("nftLoading") { LoadingBlock() }
                }
                if (!loading && nftRows.isEmpty()) {
                    item("nftEmpty") {
                        EmptyState(
                            emoji = "",
                            icon = Icons.Filled.AutoAwesome,
                            title = "Инвентарь пуст",
                            text = "Запусти массовый сбор: приложение пройдёт по коллекциям " +
                                "уникальных подарков и запишет владельцев — и тех, что лежат " +
                                "в кошельках, и тех, что остались в Telegram.",
                        )
                    }
                }
                if (nftRows.isEmpty() && !loading) {
                    item("nftEmpty") {
                        HdCard(contentPadding = PaddingValues(16.dp)) {
                            Text(
                                if (nftQuery.isBlank() && nftCollection.isBlank() && !nftResale)
                                    "В базе пока пусто"
                                else "Ничего не нашлось",
                                style = MaterialTheme.typography.titleSmall,
                                color = Hd.Text,
                            )
                            Spacer(Modifier.height(4.dp))
                            Text(
                                "Ищи по нику или id владельца, названию коллекции " +
                                    "(PlushPepe), номеру подарка или адресу кошелька. " +
                                    "База пополняется обходами — запусти сбор ниже, и " +
                                    "результат сразу появится здесь.",
                                style = MaterialTheme.typography.bodySmall,
                                color = Hd.Muted,
                            )
                            if (nftQuery.isNotBlank() || nftCollection.isNotBlank() || nftResale) {
                                Spacer(Modifier.height(8.dp))
                                TextButton(onClick = {
                                    nftQuery = ""
                                    nftCollection = ""
                                    nftResale = false
                                }) {
                                    Text(
                                        "Сбросить фильтры",
                                        color = Hd.Accent2,
                                        style = MaterialTheme.typography.labelMedium,
                                    )
                                }
                            }
                        }
                    }
                }
                items(nftRows, key = { "nft-${it.slug}" }) { item ->
                    NftItemCard(item = item, api = api, onToast = onToast, onOpenProfile = onOpenProfile)
                }

                if (nftHasMore) {
                    item("nftMore") {
                        OutlinedButton(
                            onClick = { loadMoreNft() },
                            enabled = !nftMoreBusy,
                            shape = RoundedCornerShape(12.dp),
                            modifier = Modifier.fillMaxWidth().heightIn(min = 44.dp),
                        ) {
                            if (nftMoreBusy) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(16.dp),
                                    strokeWidth = 2.dp,
                                    color = Hd.Accent2,
                                )
                                Spacer(Modifier.width(8.dp))
                            }
                            Text(
                                if (nftMoreBusy) "Загружаю…" else "Показать ещё",
                                color = Hd.Accent2,
                                style = MaterialTheme.typography.labelLarge,
                            )
                        }
                    }
                }
            }

            if (mode == 2) {
                item("ownersSearch") {
                    HdCard(contentPadding = PaddingValues(12.dp)) {
                        HdTextField(
                            value = ownerQuery,
                            onValueChange = { ownerQuery = it.take(80) },
                            label = "Поиск владельцев",
                            hint = "ник, имя или id",
                            singleLine = true,
                            trailing = if (ownerQuery.isNotEmpty()) {
                                {
                                    IconButton(onClick = { ownerQuery = "" }) {
                                        Icon(
                                            Icons.Filled.Clear,
                                            contentDescription = "Очистить поиск",
                                            tint = Hd.Muted,
                                            modifier = Modifier.size(18.dp),
                                        )
                                    }
                                }
                            } else {
                                null
                            },
                        )
                        Spacer(Modifier.height(6.dp))
                        Text(
                            "Показано ${compactNumber(ownerTotal)} владельцев и отложенных профилей",
                            style = MaterialTheme.typography.labelSmall,
                            color = Hd.Dim,
                        )
                    }
                }
                item("profilesForm") {
                    HdCard {
                        Text(
                            "Шаг 2: профили владельцев",
                            style = MaterialTheme.typography.titleSmall,
                            color = Hd.Text,
                        )
                        Spacer(Modifier.height(4.dp))
                        Text(
                            "У каждого владельца NFT есть дарители. Читаем их подарки — так база " +
                                "растёт по живой сети и подтягивает новых людей, а не только тех, " +
                                "кто попал в инвентарь.",
                            style = MaterialTheme.typography.bodySmall,
                            color = Hd.Muted,
                        )
                        Spacer(Modifier.height(8.dp))
                        Row(
                            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()),
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            listOf(100, 500, 2_000, 10_000).forEach { value ->
                                FilterChip(
                                    selected = profileBudget == value,
                                    onClick = { profileBudget = value },
                                    label = { Text("${compactNumber(value)} профилей") },
                                    colors = giftChipColors(),
                                )
                            }
                        }
                        Spacer(Modifier.height(10.dp))
                        OutlinedButton(
                            onClick = { startProfiles() },
                            enabled = !busy && job == null && nftSummary.owners > 0,
                            shape = RoundedCornerShape(12.dp),
                            modifier = Modifier.fillMaxWidth().heightIn(min = 46.dp),
                        ) {
                            Icon(
                                Icons.Filled.Person,
                                contentDescription = null,
                                tint = Hd.Accent2,
                                modifier = Modifier.size(18.dp),
                            )
                            Spacer(Modifier.width(6.dp))
                            Text(
                                if (nftSummary.owners > 0) "Собрать профили владельцев" else "Сначала соберите инвентарь",
                                color = Hd.Accent2,
                                style = MaterialTheme.typography.labelLarge,
                            )
                        }
                    }
                }
                if (loading && nftOwnersList.isEmpty()) {
                    item("ownersLoading") { LoadingBlock() }
                }
                if (!loading && nftOwnersList.isEmpty()) {
                    item("ownersEmpty") {
                        EmptyState(
                            emoji = "",
                            icon = Icons.Filled.AutoAwesome,
                            title = "Владельцев пока нет",
                            text = "После массового обхода здесь появятся все, у кого нашлись NFT — " +
                                "с числом вещей и раскладкой «кошелёк / Telegram».",
                        )
                    }
                }
                items(nftOwnersList, key = { "owner-${it.tgId}" }) { owner ->
                    NftOwnerCard(owner = owner, onToast = onToast, onOpenProfile = { selectedOwner = owner })
                }
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


/**
 * Плашка подарка вместо общей иконки: цвет и узор зависят от коллекции,
 * внутри — первые буквы названия. Узнаётся с первого взгляда, как в Telegram.
 */
@Composable
private fun GiftBadge(
    title: String,
    token: String,
    size: Dp = 42.dp,
) {
    val palette = listOf(
        Color(0xFF8E7BFF) to Color(0xFF5B8CFF),
        Color(0xFFFF9A6C) to Color(0xFFFFD166),
        Color(0xFF4DD0E1) to Color(0xFF7C9BFF),
        Color(0xFFF56CA0) to Color(0xFFB98CFF),
        Color(0xFF7BD88F) to Color(0xFFCBE86B),
        Color(0xFFFFB74D) to Color(0xFFF06292),
        Color(0xFF64B5F6) to Color(0xFF4DB6AC),
    )
    val seed = (token.ifBlank { title }).hashCode()
    val pair = palette[((seed % palette.size) + palette.size) % palette.size]
    val letters = title.trim()
        .split(" ")
        .filter { it.isNotBlank() }
        .take(2)
        .map { it.first().uppercaseChar() }
        .joinToString("")
    Box(
        Modifier
            .size(size)
            .clip(RoundedCornerShape(14.dp))
            .background(Brush.linearGradient(listOf(pair.first, pair.second))),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            letters.ifBlank { "★" },
            color = Color.White,
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.Bold,
            maxLines = 1,
        )
    }
}

@Composable
private fun GiftCard(
    gift: GiftItem,
    api: Api?,
    onToast: (String) -> Unit,
    onOpenProfile: (String) -> Unit,
) {
    val context = LocalContext.current
    var detailsOpen by remember(gift.id) { mutableStateOf(false) }
    val ownerKey = gift.ownerUsername?.let { "@$it" } ?: gift.ownerTgId.toString()
    HdCard(
        onClick = { detailsOpen = true },
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
            GiftArtwork(slug = gift.slug, title = gift.title, size = 68.dp) {
                GiftBadge(title = gift.title, token = gift.slug ?: gift.title, size = 68.dp)
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
                if (gift.presence != "visible") {
                    Text(
                        when (gift.presence) {
                            "previous_owner" -> "Предыдущий владелец"
                            "hidden" -> "Не виден в профиле · возможно скрыт"
                            else -> "Не виден в профиле · скрыт или продан"
                        },
                        style = MaterialTheme.typography.labelSmall,
                        color = Hd.Warn,
                    )
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
                if (gift.isNft) {
                    Spacer(Modifier.height(6.dp))
                    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        if (gift.num > 0) Chip("#${gift.num}", Hd.Accent)
                        Chip(if (gift.onChain) "кошелёк" else "Telegram", if (gift.onChain) Hd.Warn else Hd.Accent2)
                        gift.priceLabel?.let { Chip(it, Hd.Warn) }
                        if (gift.availabilityTotal > 0) {
                            Chip(
                                "${gift.availabilityIssued}/${gift.availabilityTotal}",
                                Hd.Dim,
                            )
                        }
                    }
                    Spacer(Modifier.height(2.dp))
                    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        TextButton(
                            onClick = { onOpenProfile(ownerKey) },
                            contentPadding = PaddingValues(horizontal = 6.dp, vertical = 0.dp),
                        ) {
                            Text(
                                "Профиль владельца",
                                color = Hd.Accent2,
                                style = MaterialTheme.typography.labelMedium,
                            )
                        }
                        gift.nftLink?.let { link ->
                            TextButton(
                                onClick = {
                                    if (!openLink(context, link)) {
                                        copyText(context, "nft", link)
                                        onToast("Ссылка на NFT скопирована")
                                    }
                                },
                                contentPadding = PaddingValues(horizontal = 6.dp, vertical = 0.dp),
                            ) {
                                Text(
                                    "Открыть NFT",
                                    color = Hd.Accent,
                                    style = MaterialTheme.typography.labelMedium,
                                )
                            }
                        }
                    }
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
    if (detailsOpen) {
        GiftDetailsDialog(gift, api, onDismiss = { detailsOpen = false })
    }
}

/** Карточка одной NFT-вещи: где лежит, сколько стоит, чья и по какой ссылке. */
@Composable
private fun NftItemCard(
    item: NftItem,
    api: Api?,
    onToast: (String) -> Unit,
    onOpenProfile: (String) -> Unit,
) {
    val context = LocalContext.current
    var detailsOpen by remember(item.slug) { mutableStateOf(false) }
    HdCard(
        onClick = { detailsOpen = true },
        onLongClick = {
            val payload = buildString {
                append(item.title)
                if (item.num > 0) append(" #${item.num}")
                append(" · ${item.slug}")
                append(" · ${item.chainLabel}")
                append(" · ${item.ownerHandle}")
                item.priceLabel?.let { append(" · $it") }
            }
            copyText(context, "nft", payload)
            onToast("NFT скопирован")
        },
        contentPadding = PaddingValues(14.dp),
    ) {
        Row(verticalAlignment = Alignment.Top) {
            GiftArtwork(slug = item.slug, title = item.title, size = 68.dp) {
                GiftBadge(title = item.title, token = item.slug, size = 68.dp)
            }
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        item.title,
                        style = MaterialTheme.typography.titleSmall,
                        color = Hd.Text,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                    if (item.num > 0) {
                        Spacer(Modifier.width(6.dp))
                        Text(
                            "#${item.num}",
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
                    item.ownerHandle,
                    style = MaterialTheme.typography.labelSmall,
                    color = Hd.Muted,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                if (item.ownerPresence == "hidden" || item.ownerPresence == "previous_owner") {
                    Text("Не виден в профиле · владелец уточняется", color = Hd.Warn, style = MaterialTheme.typography.labelSmall)
                }
                Spacer(Modifier.height(5.dp))
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    Chip(
                        if (item.onChain) "на кошельке" else "в Telegram",
                        if (item.onChain) Hd.Warn else Hd.Accent2,
                    )
                    item.priceLabel?.let { Chip(it, Hd.Accent) }
                    if (item.availabilityTotal > 0) {
                        Chip("${item.availabilityIssued}/${item.availabilityTotal}", Hd.Dim)
                    }
                }
                Spacer(Modifier.height(2.dp))
                Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    TextButton(
                        onClick = { onOpenProfile(item.ownerUsername?.let { "@$it" } ?: item.ownerTgId.toString()) },
                        contentPadding = PaddingValues(horizontal = 6.dp, vertical = 0.dp),
                    ) {
                        Text(
                            "Профиль владельца",
                            color = Hd.Accent2,
                            style = MaterialTheme.typography.labelMedium,
                        )
                    }
                    TextButton(
                        onClick = {
                            if (!openLink(context, item.nftLink)) {
                                copyText(context, "nft", item.nftLink)
                                onToast("Ссылка на NFT скопирована")
                            }
                        },
                        contentPadding = PaddingValues(horizontal = 6.dp, vertical = 0.dp),
                    ) {
                        Text(
                            "Открыть NFT",
                            color = Hd.Accent,
                            style = MaterialTheme.typography.labelMedium,
                        )
                    }
                }
            }
            Spacer(Modifier.width(6.dp))
            Text(
                relativeTime(item.seenAt),
                style = MaterialTheme.typography.labelSmall,
                color = Hd.Dim,
                fontFamily = FontFamily.Monospace,
            )
        }
    }
    if (detailsOpen) {
        GiftDetailsDialog(
            gift = GiftItem(
                id = 0, ownerTgId = item.ownerTgId, ownerUsername = item.ownerUsername,
                ownerName = item.ownerName, title = item.title, kind = "unique", stars = 0,
                fromTgId = 0, fromUsername = null, fromName = null, message = null,
                date = item.seenAt, isNft = true, nameHidden = false, slug = item.slug,
                num = item.num, valueAmount = item.valueAmount, valueCurrency = item.valueCurrency,
                resellAmount = item.resellAmount, availabilityIssued = item.availabilityIssued,
                availabilityTotal = item.availabilityTotal, onChain = item.onChain,
                giftAddress = item.giftAddress, ownerAddress = item.ownerAddress,
                valueUsd = item.valueUsd, burned = item.burned,
            ),
            api = api,
            onDismiss = { detailsOpen = false },
        )
    }
}

/** Владелец NFT: почти любой пользователь, встреченный при обходе сети. */
@Composable
private fun NftOwnerCard(
    owner: NftOwner,
    onToast: (String) -> Unit,
    onOpenProfile: (String) -> Unit,
) {
    val context = LocalContext.current
    HdCard(
        onClick = { onOpenProfile(owner.username?.let { "@$it" } ?: owner.tgId.toString()) },
        onLongClick = {
            copyText(context, "owner", "${owner.handle} · id ${owner.tgId} · NFT ${owner.nft}")
            onToast("Владелец скопирован")
        },
        contentPadding = PaddingValues(14.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier
                    .size(38.dp)
                    .background(Hd.Good.copy(alpha = 0.16f), RoundedCornerShape(12.dp)),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    imageVector = Icons.Filled.Person,
                    contentDescription = null,
                    tint = Hd.Good,
                    modifier = Modifier.size(20.dp),
                )
            }
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    owner.handle,
                    style = MaterialTheme.typography.titleSmall,
                    color = Hd.Text,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    "id ${owner.tgId}",
                    style = MaterialTheme.typography.labelSmall,
                    color = Hd.Dim,
                    fontFamily = FontFamily.Monospace,
                )
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(
                    if (owner.deferredCount >= 1000) "${owner.deferredCount} подарков · отложено" else "${owner.gifts} подарков · ${owner.nft} NFT",
                    style = MaterialTheme.typography.labelLarge,
                    color = Hd.Accent,
                )
                Text(
                    "${owner.onChain} / ${owner.hosted}",
                    style = MaterialTheme.typography.labelSmall,
                    color = Hd.Dim,
                )
            }
        }
    }
}


@Composable
private fun OwnerGiftsScreen(
    api: Api?, owner: NftOwner, onBack: () -> Unit, onToast: (String) -> Unit,
    onOpenProfile: (String) -> Unit,
) {
    var rows by remember(owner.tgId) { mutableStateOf<List<GiftItem>>(emptyList()) }
    var inventory by remember(owner.tgId) { mutableStateOf<List<NftItem>>(emptyList()) }
    var profile by remember(owner.tgId) { mutableStateOf<org.json.JSONObject?>(null) }
    var photo by remember(owner.tgId) { mutableStateOf<ImageBitmap?>(null) }
    var error by remember(owner.tgId) { mutableStateOf<String?>(null) }
    var loading by remember(owner.tgId) { mutableStateOf(true) }
    LaunchedEffect(owner.tgId) {
        if (api == null) { loading = false; return@LaunchedEffect }
        runCatching { api.gifts(owner = owner.tgId, limit = 1000) }
            .onSuccess { rows = it.rows }
            .onFailure { error = it.message }
        runCatching { api.giftOwnerNft(owner.tgId) }.onSuccess { inventory = it }
        loading = false
    }
    LaunchedEffect(owner.tgId) {
        if (api == null) return@LaunchedEffect
        runCatching { api.giftOwnerProfile(owner.tgId, owner.username) }.onSuccess { result ->
            profile = result
            result.str("photo")?.let { raw ->
                photo = runCatching {
                    val bytes = Base64.decode(raw, Base64.DEFAULT)
                    BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
                }.getOrNull()
            }
        }.onFailure { error = it.message }
    }
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            TextButton(onClick = onBack) { Text("Назад к владельцам") }
            HdCard(contentPadding = PaddingValues(16.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    val avatar = photo
                    if (avatar != null) {
                        Image(avatar, contentDescription = "Аватар владельца", modifier = Modifier.size(64.dp).clip(RoundedCornerShape(18.dp)))
                    } else {
                        Icon(Icons.Filled.Person, contentDescription = null, tint = Hd.Good, modifier = Modifier.size(64.dp))
                    }
                    Spacer(Modifier.width(12.dp))
                    Column(Modifier.weight(1f)) {
                        val fullName = listOfNotNull(profile?.str("first_name"), profile?.str("last_name")).joinToString(" ")
                        Text(fullName.ifBlank { owner.name ?: owner.handle }, style = MaterialTheme.typography.titleMedium, color = Hd.Text)
                        Text(profile?.str("username")?.let { "@$it" } ?: owner.handle, color = Hd.Muted)
                        Text("ID ${owner.tgId}", style = MaterialTheme.typography.labelSmall, color = Hd.Dim)
                    }
                }
                profile?.str("bio")?.let { Text(it, modifier = Modifier.padding(top = 8.dp), color = Hd.Text) }
                Text("Найдено ${rows.size} подарков в локальной базе", modifier = Modifier.padding(top = 8.dp), color = Hd.Muted)
                if (rows.size >= 1000) Text("Показаны первые 1000 записей", color = Hd.Warn)
            }
        }
        error?.let { item { Text(it, color = Hd.Warn, style = MaterialTheme.typography.bodySmall) } }
        if (loading) item { LoadingBlock() }
        if (!loading && rows.isEmpty() && inventory.isEmpty()) item {
            Text("Для этого профиля пока нет сохранённых подарков. Данные профиля получаются из Telegram при открытии.", color = Hd.Muted)
        }
        items(rows, key = { "gift-${it.id}" }) { gift ->
            GiftCard(gift, api, onToast, onOpenProfile)
        }
        val knownSlugs = rows.mapNotNull { it.slug }.toSet()
        items(inventory.filterNot { it.slug in knownSlugs }, key = { "inventory-${it.slug}" }) { nft ->
            NftItemCard(nft, api, onToast, onOpenProfile)
        }
    }
}

@Composable
private fun GiftDetailsDialog(gift: GiftItem, api: Api?, onDismiss: () -> Unit) {
    var history by remember(gift.slug) { mutableStateOf<List<org.json.JSONObject>>(emptyList()) }
    LaunchedEffect(gift.slug) {
        val slug = gift.slug ?: return@LaunchedEffect
        if (api != null) history = runCatching { api.giftHistory(slug) }.getOrDefault(emptyList())
    }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(gift.title) },
        text = {
            Column(Modifier.heightIn(max = 440.dp).verticalScroll(rememberScrollState())) {
                GiftArtwork(gift.slug, gift.title, 120.dp) {
                    GiftBadge(title = gift.title, token = gift.slug ?: gift.title, size = 120.dp)
                }
                Spacer(Modifier.height(8.dp))
                Text("Владелец: ${gift.ownerName ?: gift.ownerHandle} · ID ${gift.ownerTgId}")
                Text("От: ${gift.fromName ?: gift.fromUsername ?: if (gift.nameHidden) "аноним" else "неизвестно"}")
                gift.message?.let { Text("Описание: $it") }
                gift.slug?.let { Text("NFT: https://t.me/nft/$it") }
                gift.priceLabel?.let { Text("Стоимость: $it") }
                gift.attributes?.let { raw ->
                    val attributes = runCatching { org.json.JSONArray(raw) }.getOrNull()
                    if (attributes != null) for (i in 0 until attributes.length()) {
                        val attr = attributes.optJSONObject(i) ?: continue
                        val label = when (attr.optString("type")) {
                            "StarGiftAttributeModel" -> "Модель"
                            "StarGiftAttributeBackdrop" -> "Фон"
                            "StarGiftAttributePattern" -> "Узор"
                            else -> "Атрибут"
                        }
                        val rarity = attr.optInt("rarity", -1)
                        Text("$label: ${attr.optString("name")}" + if (rarity >= 0) " · ${rarity / 10.0}%" else "")
                    }
                }
                if (history.isNotEmpty()) {
                    Spacer(Modifier.height(10.dp))
                    Text("История наблюдений", style = MaterialTheme.typography.titleSmall)
                    Text("Даты — время обнаружения, а не точное время передачи.", style = MaterialTheme.typography.bodySmall)
                    history.forEach { row ->
                        val who = row.str("owner_name") ?: row.str("owner_username") ?: "ID ${row.num("owner_tg_id")}"
                        Text("$who · ${relativeTime(row.num("observed_at"))}", style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Закрыть") } },
    )
}
