package net.syslikoff.healdragg.ui

import android.net.Uri
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private fun searchInput(raw: String): Pair<String, Boolean> {
    val value = raw.trim()
    val asUrl = if (value.startsWith("t.me/", true) || value.startsWith("telegram.me/", true)) "https://$value" else value
    val uri = runCatching { Uri.parse(asUrl) }.getOrNull()
    val telegram = uri?.host?.lowercase() in setOf("t.me", "www.t.me", "telegram.me", "www.telegram.me")
    if (telegram) {
        val parts = uri?.pathSegments.orEmpty()
        if (parts.size == 2 && parts[0].equals("nft", ignoreCase = true)) return parts[1] to false
        if (parts.size == 1) return parts[0] to true
        return value to false
    }
    val isProfile = Regex("^@?[A-Za-z0-9_]{1,32}$").matches(value)
    return value to isProfile
}

@Composable
fun SearchScreen(
    vm: AppViewModel,
    onOpenTarget: (Long) -> Unit,
    onOpenProfile: (String) -> Unit,
    onToast: (String) -> Unit,
    onOpenAccounts: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var query by rememberSaveable { mutableStateOf(vm.targetQuery) }
    var map by rememberSaveable { mutableStateOf(false) }
    var giftResults by remember { mutableStateOf<GiftSearchResult?>(null) }
    var giftError by remember { mutableStateOf<String?>(null) }
    var giftLoading by remember { mutableStateOf(false) }
    var resultQuery by remember { mutableStateOf("") }
    var nftBusy by remember { mutableStateOf(false) }
    var liveNft by remember { mutableStateOf<Pair<String, NftItem>?>(null) }
    var liveError by remember { mutableStateOf<Pair<String, String>?>(null) }
    val (normalized, canOpenProfile) = searchInput(query)
    val client = vm.api
    val pending = normalized != vm.targetQuery
    val canLookupNft = Regex("^[A-Za-z0-9_]+-[1-9][0-9]*$").matches(normalized)

    LaunchedEffect(normalized, client) {
        if (normalized != vm.targetQuery) {
            delay(300)
            vm.changeTargetQuery(normalized)
        }
        giftResults = null
        giftError = null
        resultQuery = normalized
        if (normalized.isBlank() || client == null) { giftLoading = false; return@LaunchedEffect }
        giftLoading = true
        try { giftResults = client.searchGifts(normalized) }
        catch (exc: CancellationException) { throw exc }
        catch (exc: Exception) { giftError = exc.message ?: "Не удалось найти подарки" }
        finally { giftLoading = false }
    }
    LaunchedEffect(client) {
        if (client != null && vm.targets.isEmpty() && !vm.targetPageLoading) vm.refresh(force = true)
    }

    if (map) {
        GlobalMapScreen(api = client, onBack = { map = false }, onOpenProfile = onOpenProfile, onToast = onToast)
        return
    }

    RefreshableBox(refreshing = vm.refreshing, onRefresh = { vm.refresh(force = true) }, modifier = Modifier.fillMaxSize()) {
        LazyColumn(modifier = Modifier.fillMaxSize(), contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            item("intro") {
                Text("Поиск и анализ", style = MaterialTheme.typography.headlineSmall, color = Hd.Text)
                Text("Профиль Telegram, имя, ID или название и ссылка подарка", style = MaterialTheme.typography.bodySmall, color = Hd.Muted)
            }
            item("search") {
                OutlinedTextField(value = query, onValueChange = { query = it.take(128) }, modifier = Modifier.fillMaxWidth(),
                    singleLine = true, label = { Text("@username, ID, имя или подарок") },
                    leadingIcon = { androidx.compose.material3.Icon(Icons.Filled.Search, contentDescription = null) })
            }
            if (normalized.isNotBlank() && canOpenProfile) item("resolve") {
                Button(onClick = { onOpenProfile(normalized) }, modifier = Modifier.fillMaxWidth()) { Text("Открыть профиль по @username или ID") }
                Text("Сначала ищем в базе, затем — в Telegram через подключённый аккаунт. Имя само по себе не идентифицирует пользователя; неизвестный ID может открыться без данных.", color = Hd.Dim, style = MaterialTheme.typography.bodySmall)
            }
            if (canLookupNft) item("resolve-nft") {
                OutlinedButton(onClick = {
                    val slug = normalized
                    if (client != null && !nftBusy) {
                        nftBusy = true
                        liveError = null
                        scope.launch {
                            try { liveNft = slug to client.lookupNft(slug) }
                            catch (exc: CancellationException) { throw exc }
                            catch (exc: Exception) { liveError = slug to (exc.message ?: "Не удалось найти NFT") }
                            finally { nftBusy = false }
                        }
                    } else if (client == null) onToast("Движок не запущен")
                }, enabled = !nftBusy, modifier = Modifier.fillMaxWidth()) { Text(if (nftBusy) "Ищу NFT в Telegram…" else "Найти этот NFT в Telegram") }
            }
            liveError?.takeIf { it.first == normalized }?.let { failure -> item("nft-error") { ErrorBanner(failure.second) } }
            liveNft?.takeIf { it.first == normalized }?.let { found -> item("nft-live") { NftItemCard(found.second, client, onToast, onOpenProfile) } }
            if (!vm.hasSessions && vm.engine == EngineState.Ready) item("connect") {
                OutlinedButton(onClick = onOpenAccounts, modifier = Modifier.fillMaxWidth()) { Text("Подключить аккаунт Telegram") }
            }
            item("map") {
                OutlinedButton(onClick = { map = true }, modifier = Modifier.fillMaxWidth()) { Text("Общая карта собранных связей") }
            }
            item("profiles-heading") {
                Text(if (normalized.isBlank()) "Сохранённые профили" else "Профили в базе", style = MaterialTheme.typography.titleMedium, color = Hd.Text)
            }
            vm.targetPageError?.let { msg -> item("profiles-error") { ErrorBanner(msg, onRetry = { if (vm.targetNextBefore == null) vm.reloadTargets() else vm.loadMoreTargets() }) } }
            if ((vm.targetPageLoading || pending) && vm.targets.isEmpty()) item("profiles-loading") { LoadingBlock() }
            if (!pending) {
                items(vm.targets, key = { "profile-${it.tgId}" }) { profile ->
                    HdCard(onClick = { onOpenTarget(profile.tgId) }, contentPadding = PaddingValues(14.dp)) {
                        Text(profile.displayName, style = MaterialTheme.typography.titleSmall, color = Hd.Text)
                        Text(profile.username?.let { "@$it · ID ${profile.tgId}" } ?: "ID ${profile.tgId}", color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
                        Text("${profile.groupsCount} чатов · ${profile.giftsCount} записей подарков", color = Hd.Dim, style = MaterialTheme.typography.labelSmall)
                    }
                }
                if (!vm.targetPageLoading && vm.targetPageError == null && vm.targets.isEmpty()) item("profiles-empty") {
                    Text(if (normalized.isBlank()) "Пока нет сохранённых профилей." else "Профилей в базе не найдено.", color = Hd.Muted)
                }
                if (vm.targetNextBefore != null) item("profiles-more") {
                    OutlinedButton(onClick = { vm.loadMoreTargets() }, enabled = !vm.targetPageLoading, modifier = Modifier.fillMaxWidth()) { Text(if (vm.targetPageLoading) "Загружаю…" else "Показать ещё профили") }
                }
            }
            if (normalized.isNotBlank()) {
                item("gift-heading") {
                    Text("Подарки и NFT в базе", style = MaterialTheme.typography.titleMedium, color = Hd.Text)
                    Text("Поиск только по уже собранным записям; отсутствие результата не означает, что подарка нет.", style = MaterialTheme.typography.bodySmall, color = Hd.Muted)
                }
                if (giftLoading || resultQuery != normalized) item("gifts-loading") { LoadingBlock() }
                giftError?.let { msg -> item("gifts-error") { ErrorBanner(msg) } }
                if (resultQuery == normalized) {
                    val result = giftResults
                    if (result != null && result.gifts.isEmpty() && result.nft.isEmpty()) item("gifts-empty") { Text("В сохранённых подарках не найдено.", color = Hd.Muted) }
                    if (result != null) {
                        items(result.gifts, key = { "gift-${it.id}" }) { gift -> GiftCard(gift, client, onToast, onOpenProfile) }
                        val slugs = result.gifts.mapNotNull { it.slug }.toSet()
                        items(result.nft.filterNot { it.slug in slugs }, key = { "nft-${it.slug}" }) { nft -> NftItemCard(nft, client, onToast, onOpenProfile) }
                    }
                }
            }
        }
    }
}
