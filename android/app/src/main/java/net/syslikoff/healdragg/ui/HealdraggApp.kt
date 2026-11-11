package net.syslikoff.healdragg.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CardGiftcard
import androidx.compose.material.icons.filled.Explore
import androidx.compose.material.icons.filled.GpsFixed
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.TrackChanges
import androidx.compose.material.icons.outlined.CardGiftcard
import androidx.compose.material.icons.outlined.Explore
import androidx.compose.material.icons.outlined.GpsFixed
import androidx.compose.material.icons.outlined.Notifications
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.NavigationRailItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

private enum class Tab(
    val title: String,
    val icon: ImageVector,
    val selectedIcon: ImageVector,
) {
    Targets("Поиск", Icons.Outlined.GpsFixed, Icons.Filled.GpsFixed),
    Alerts("Алерты", Icons.Outlined.Notifications, Icons.Filled.Notifications),
    Catalog("База", Icons.Outlined.Explore, Icons.Filled.Explore),
    Gifts("Подарки", Icons.Outlined.CardGiftcard, Icons.Filled.CardGiftcard),
    Settings("Настройки", Icons.Outlined.Settings, Icons.Filled.Settings),
}

/** Стеклянный контейнер для полноэкранных сценариев (вход, мастер настройки). */
@Composable
private fun OverlayScreen(snackbar: SnackbarHostState, content: @Composable () -> Unit) {
    Box(Modifier.fillMaxSize().background(Hd.Bg)) {
        content()
        SnackbarHost(
            hostState = snackbar,
            modifier = Modifier.align(Alignment.BottomCenter).padding(12.dp).navigationBarsPadding(),
        )
    }
}

/**
 * Корень приложения: адаптивный каркас (телефон — нижние табы, планшет — rail
 * и две колонки), общая шапка, вкладки и единый snackbar.
 */
@Composable
fun HealdraggApp(vm: AppViewModel, onOpenBattery: () -> Unit) {
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    val toast: (String) -> Unit = { message ->
        scope.launch { snackbar.showSnackbar(message) }
    }

    var tab by rememberSaveable { mutableStateOf(Tab.Targets) }
    var openTarget by rememberSaveable { mutableStateOf<Long?>(null) }
    val historySaver = remember { listSaver<List<Long>, Long>(save = { it }, restore = { it.toList() }) }
    var profileHistory by rememberSaveable(stateSaver = historySaver) { mutableStateOf(emptyList<Long>()) }
    val screenStates = rememberSaveableStateHolder()
    var searchMapOpen by rememberSaveable { mutableStateOf(false) }
    var navigationVersion by remember { mutableStateOf(0) }
    val showProfile: (Long) -> Unit = { id ->
        if (openTarget != id) {
            openTarget?.let { profileHistory = profileHistory + it }
            openTarget = id
        }
        navigationVersion++
    }
    val backProfile: () -> Unit = {
        val closing = openTarget
        if (profileHistory.isNotEmpty()) {
            openTarget = profileHistory.last()
            profileHistory = profileHistory.dropLast(1)
        } else openTarget = null
        if (closing != null && closing != openTarget && closing !in profileHistory) screenStates.removeState("profile-$closing")
        navigationVersion++
    }
    var showSetup by rememberSaveable { mutableStateOf(vm.isLocal && !vm.hasCredentials) }
    var showAccounts by rememberSaveable { mutableStateOf(false) }

    // Сохранённый профиль открывается без сети; неизвестный разрешается в Telegram.
    var openingProfile by remember { mutableStateOf(false) }
    val openProfile: (String) -> Unit = { key ->
        if (!openingProfile) {
            val client = vm.api
            if (client == null) toast("Движок не запущен")
            else {
                openingProfile = true
                val requestedNavigation = navigationVersion
                scope.launch {
                    try {
                        val existing = try { client.findTarget(key) } catch (exc: ApiException) {
                            if (exc.code == 404) null else throw exc
                        }
                        if (navigationVersion != requestedNavigation) {
                            openingProfile = false
                        } else if (existing != null) {
                            showProfile(existing.tgId)
                            openingProfile = false
                        } else {
                            vm.addTarget(key) { result ->
                                openingProfile = false
                                if (navigationVersion == requestedNavigation) {
                                    result.onSuccess { target -> showProfile(target.tgId) }
                                        .onFailure { toast(it.message ?: "Не удалось открыть профиль") }
                                }
                            }
                        }
                    } catch (exc: CancellationException) {
                        openingProfile = false
                        throw exc
                    } catch (exc: Exception) {
                        openingProfile = false
                        toast(exc.message ?: "Не удалось открыть профиль")
                    }
                }
            }
        }
    }

    @Composable
    fun ProfileScreen(tgId: Long) {
        screenStates.SaveableStateProvider("profile-$tgId") {
            TargetDetailScreen(vm = vm, tgId = tgId, onBack = backProfile,
                onToast = toast, onOpenProfile = openProfile)
        }
    }

    BackHandler(enabled = openTarget != null) { backProfile() }
    BackHandler(enabled = openTarget == null && showSetup && (vm.hasCredentials || !vm.isLocal)) {
        showSetup = false
    }
    BackHandler(enabled = showAccounts) { showAccounts = false }

    if (showAccounts) {
        OverlayScreen(snackbar) {
            AccountsScreen(vm = vm, onBack = { showAccounts = false }, onToast = toast)
        }
        return
    }

    if (showSetup) {
        val closeSetup: (() -> Unit)? =
            if (vm.hasCredentials || !vm.isLocal) ({ showSetup = false }) else null
        OverlayScreen(snackbar) {
            SetupScreen(
                vm = vm,
                onToast = toast,
                onOpenBattery = onOpenBattery,
                onDone = {
                    showSetup = false
                    vm.refresh(force = true)
                },
                onOpenAccounts = { showAccounts = true },
                onBack = closeSetup,
            )
        }
        return
    }

    BoxWithConstraints(Modifier.fillMaxSize().background(Hd.Bg)) {
        val wide = maxWidth >= 800.dp
        val listWidth = (maxWidth * 0.36f).coerceIn(320.dp, 430.dp)
        val title = when {
            openTarget != null -> "Профиль"
            else -> tab.title
        }

        if (wide) {
            Row(Modifier.fillMaxSize().navigationBarsPadding()) {
                NavigationRail(
                    containerColor = Hd.Surface,
                    header = { RailHeader() },
                ) {
                    Spacer(Modifier.height(10.dp))
                    Tab.entries.forEach { item ->
                        NavigationRailItem(
                            selected = tab == item && openTarget == null,
                            onClick = {
                                openTarget = null
                                profileHistory = emptyList()
                                navigationVersion++
                                tab = item
                            },
                            icon = { TabIcon(item, vm, tab == item) },
                            label = {
                                Text(
                                    item.title,
                                    style = MaterialTheme.typography.labelMedium,
                                    maxLines = 1,
                                )
                            },
                            colors = railColors(),
                        )
                    }
                }
                Column(Modifier.weight(1f).fillMaxHeight()) {
                    HdTopBar(
                        vm = vm,
                        title = title,
                        onRefresh = { vm.refresh(force = true) },
                        onSettings = {
                            openTarget = null
                            profileHistory = emptyList()
                            navigationVersion++
                            tab = Tab.Settings
                        },
                    )
                    Box(Modifier.weight(1f)) {
                        when (tab) {
                            Tab.Catalog -> {
                                val catalogTarget = openTarget
                                if (catalogTarget != null) {
                                    ProfileScreen(catalogTarget)
                                } else {
                                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.TopCenter) {
                                        Box(Modifier.widthIn(max = 900.dp).fillMaxWidth()) {
                                            CatalogScreen(
                                                vm = vm,
                                                onOpenTarget = showProfile,
                                                onToast = toast,
                                                onOpenAccounts = { showAccounts = true },
                                            )
                                        }
                                    }
                                }
                            }
                            Tab.Gifts -> {
                                val giftTarget = openTarget
                                if (giftTarget != null) {
                                    ProfileScreen(giftTarget)
                                } else {
                                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.TopCenter) {
                                        Box(Modifier.widthIn(max = 820.dp).fillMaxWidth()) {
                                            screenStates.SaveableStateProvider("gifts") {
                                                GiftsScreen(vm = vm, onToast = toast, onOpenProfile = openProfile, onOpenAccounts = { showAccounts = true })
                                            }
                                        }
                                    }
                                }
                            }
                            Tab.Settings -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.TopCenter) {
                                Box(Modifier.widthIn(max = 780.dp).fillMaxWidth()) {
                                    SettingsScreen(
                                        vm = vm,
                                        onOpenBattery = onOpenBattery,
                                        onToast = toast,
                                        onEditServer = { showSetup = true },
                                        onOpenAccounts = { showAccounts = true },
                                    )
                                }
                            }
                            else -> Row(Modifier.fillMaxSize()) {
                                Box(Modifier.width(listWidth).fillMaxHeight()) {
                                    if (tab == Tab.Targets) {
                                        screenStates.SaveableStateProvider("search") {
                                          SearchScreen(
                                            vm = vm,
                                            onOpenTarget = showProfile,
                                            onOpenProfile = openProfile,
                                            onToast = toast,
                                            onOpenAccounts = { showAccounts = true },
                                            mapOpen = searchMapOpen,
                                            profileOpen = openTarget != null,
                                            onMapChange = { searchMapOpen = it; navigationVersion++ },
                                          )
                                        }
                                    } else {
                                        AlertsScreen(vm, onOpenTarget = showProfile, onToast = toast)
                                    }
                                }
                                Box(Modifier.width(1.dp).fillMaxHeight().background(Hd.Stroke))
                                Box(Modifier.weight(1f).fillMaxHeight()) {
                                    if (openTarget != null) {
                                        ProfileScreen(openTarget!!)
                                    } else {
                                        EmptyDetailHint(tab)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        } else {
            Scaffold(
                containerColor = Hd.Bg,
                contentWindowInsets = WindowInsets(0, 0, 0, 0),
                snackbarHost = { SnackbarHost(snackbar) },
                topBar = {
                    HdTopBar(
                        vm = vm,
                        title = title,
                        onRefresh = { vm.refresh(force = true) },
                        onSettings = {
                            openTarget = null
                            profileHistory = emptyList()
                            navigationVersion++
                            tab = Tab.Settings
                        },
                    )
                },
                bottomBar = {
                    if (openTarget == null) {
                        NavigationBar(containerColor = Hd.Surface, tonalElevation = 0.dp) {
                            Tab.entries.forEach { item ->
                                NavigationBarItem(
                                    selected = tab == item,
                                    onClick = { navigationVersion++; profileHistory = emptyList(); tab = item },
                                    icon = { TabIcon(item, vm, tab == item) },
                                    label = {
                                        Text(
                                            item.title,
                                            style = MaterialTheme.typography.labelSmall,
                                            maxLines = 1,
                                        )
                                    },
                                    colors = NavigationBarItemDefaults.colors(
                                        selectedIconColor = if (Hd.material3) Hd.Accent else Hd.Accent,
                                        selectedTextColor = if (Hd.material3) Hd.Accent else Hd.Accent,
                                        indicatorColor = if (Hd.material3) {
                                            Hd.Accent.copy(alpha = 0.22f)
                                        } else {
                                            Hd.Accent.copy(alpha = 0.16f)
                                        },
                                        unselectedIconColor = Hd.Muted,
                                        unselectedTextColor = Hd.Muted,
                                    ),
                                )
                            }
                        }
                    }
                },
            ) { inner ->
                Box(Modifier.padding(inner)) {
                    when {
                        openTarget != null -> ProfileScreen(openTarget!!)
                        tab == Tab.Targets -> screenStates.SaveableStateProvider("search") {
                          SearchScreen(
                            vm = vm,
                            onOpenTarget = showProfile,
                            onOpenProfile = openProfile,
                            onToast = toast,
                            onOpenAccounts = { showAccounts = true },
                            mapOpen = searchMapOpen,
                            profileOpen = false,
                            onMapChange = { searchMapOpen = it; navigationVersion++ },
                          )
                        }
                        tab == Tab.Alerts -> AlertsScreen(vm, onOpenTarget = showProfile, onToast = toast)
                        tab == Tab.Catalog -> CatalogScreen(
                            vm = vm,
                            onOpenTarget = showProfile,
                            onToast = toast,
                            onOpenAccounts = { showAccounts = true },
                        )
                        tab == Tab.Gifts -> screenStates.SaveableStateProvider("gifts") {
                            GiftsScreen(vm = vm, onToast = toast, onOpenProfile = openProfile, onOpenAccounts = { showAccounts = true })
                        }
                        else -> SettingsScreen(
                            vm = vm,
                            onOpenBattery = onOpenBattery,
                            onToast = toast,
                            onEditServer = { showSetup = true },
                            onOpenAccounts = { showAccounts = true },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun EmptyDetailHint(tab: Tab) {
    Column(
        Modifier.fillMaxSize().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = androidx.compose.foundation.layout.Arrangement.Center,
    ) {
        Box(
            Modifier.size(76.dp).clip(CircleShape).background(Hd.Accent.copy(alpha = 0.12f)),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                imageVector = if (tab == Tab.Alerts) Icons.Filled.Notifications else Icons.Filled.TrackChanges,
                contentDescription = null,
                tint = Hd.Accent,
                modifier = Modifier.size(34.dp),
            )
        }
        Spacer(Modifier.height(16.dp))
        Text(
            if (tab == Tab.Alerts) "Выбери алерт слева" else "Выбери профиль слева",
            style = MaterialTheme.typography.titleMedium,
            color = Hd.Text,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(6.dp))
        Text(
            "Здесь откроется карточка: чаты, лента и паутина связей.",
            style = MaterialTheme.typography.bodySmall,
            color = Hd.Muted,
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
private fun RailHeader() {
    Column(
        Modifier.statusBarsPadding().padding(top = 14.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        BrandMark(size = 42)
    }
}

@Composable
private fun TabIcon(item: Tab, vm: AppViewModel, selected: Boolean) {
    val vector = if (selected) item.selectedIcon else item.icon
    if (item == Tab.Alerts && vm.unread > 0) {
        BadgedBox(
            badge = {
                Badge(containerColor = Hd.Bad, contentColor = Hd.Black) {
                    Text(if (vm.unread > 99) "99+" else vm.unread.toString(), fontSize = 10.sp)
                }
            },
        ) {
            Icon(vector, contentDescription = item.title, modifier = Modifier.size(23.dp))
        }
    } else {
        Icon(vector, contentDescription = item.title, modifier = Modifier.size(23.dp))
    }
}

@Composable
private fun railColors() = NavigationRailItemDefaults.colors(
    selectedIconColor = Hd.Accent,
    selectedTextColor = Hd.Accent,
    indicatorColor = Hd.Accent.copy(alpha = 0.20f),
    unselectedIconColor = Hd.Muted,
    unselectedTextColor = Hd.Muted,
)

private fun engineLabel(state: EngineState): String = when (state) {
    EngineState.Ready -> "онлайн"
    EngineState.Starting -> "запуск"
    EngineState.Error -> "ошибка"
    EngineState.Stopped -> "выключен"
    EngineState.Unknown -> "проверка"
}

private fun engineTint(state: EngineState): Color = when (state) {
    EngineState.Ready -> Hd.Good
    EngineState.Starting -> Hd.Warn
    EngineState.Error -> Hd.Bad
    else -> Hd.Dim
}

@Composable
private fun HdTopBar(
    vm: AppViewModel,
    title: String,
    onRefresh: () -> Unit,
    onSettings: () -> Unit,
) {
    Column(
        Modifier
            .fillMaxWidth()
            .background(Hd.Bg)
            .statusBarsPadding()
            .padding(start = 16.dp, end = 12.dp, top = 10.dp, bottom = 8.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            BrandMark(size = 38)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    "HEALDRAGG",
                    style = MaterialTheme.typography.labelSmall.copy(
                        letterSpacing = 1.8.sp,
                        fontWeight = FontWeight.SemiBold,
                    ),
                    color = Hd.Dim,
                    maxLines = 1,
                )
                Text(
                    text = title,
                    style = MaterialTheme.typography.headlineSmall,
                    color = Hd.Text,
                    maxLines = 1,
                )
            }
            EnginePill(vm.engine)
            Spacer(Modifier.width(6.dp))
            FilledTonalIconButton(
                onClick = onRefresh,
                enabled = !vm.refreshing,
                colors = IconButtonDefaults.filledTonalIconButtonColors(
                    containerColor = Hd.Surface2,
                    contentColor = if (vm.refreshing) Hd.Dim else Hd.Muted,
                ),
                modifier = Modifier.size(38.dp),
            ) {
                Icon(Icons.Filled.Refresh, contentDescription = "Обновить данные", modifier = Modifier.size(20.dp))
            }
            Spacer(Modifier.width(4.dp))
            FilledTonalIconButton(
                onClick = onSettings,
                colors = IconButtonDefaults.filledTonalIconButtonColors(
                    containerColor = Hd.Surface2,
                    contentColor = Hd.Muted,
                ),
                modifier = Modifier.size(38.dp),
            ) {
                Icon(Icons.Filled.Settings, contentDescription = "Настройки", modifier = Modifier.size(20.dp))
            }
        }
        if (vm.offline) {
            Spacer(Modifier.height(8.dp))
            EngineBanner(vm)
        }
    }
}

@Composable
private fun EnginePill(state: EngineState) {
    val color = engineTint(state)
    Row(
        modifier = Modifier
            .clip(RoundedCornerShape(50))
            .background(color.copy(alpha = 0.12f))
            .padding(horizontal = 9.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        EngineDot(state)
        Spacer(Modifier.width(5.dp))
        Text(
            engineLabel(state),
            style = MaterialTheme.typography.labelSmall,
            color = color,
            maxLines = 1,
        )
    }
}

@Composable
private fun EngineDot(state: EngineState) {
    val transition = rememberInfiniteTransition(label = "engine-pulse")
    val alpha by transition.animateFloat(
        initialValue = 0.35f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(900), RepeatMode.Reverse),
        label = "alpha",
    )
    val (color, desc) = when (state) {
        EngineState.Ready -> Hd.Good to "движок работает"
        EngineState.Starting -> Hd.Warn to "движок запускается"
        EngineState.Error -> Hd.Bad to "движок не запустился"
        EngineState.Stopped -> Hd.Dim to "движок остановлен"
        EngineState.Unknown -> Hd.Dim to "движок не проверен"
    }
    Box(
        Modifier
            .size(8.dp)
            .background(color.copy(alpha = if (state == EngineState.Starting) alpha else 1f), CircleShape)
            .semantics { contentDescription = desc },
    )
}

@Composable
private fun EngineBanner(vm: AppViewModel) {
    // Движок считается живым, но панель не ответила: это не «выключено», а сбой связи.
    val unreachable = vm.engine == EngineState.Ready
    HdCard(contentPadding = androidx.compose.foundation.layout.PaddingValues(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                if (unreachable) "Панель не отвечает" else "Нет связи с движком",
                style = MaterialTheme.typography.bodySmall,
                color = Hd.Muted,
                modifier = Modifier.weight(1f),
            )
            Text(
                if (unreachable) "Проверить" else "Запустить",
                color = Hd.Accent,
                style = MaterialTheme.typography.labelLarge,
                modifier = Modifier
                    .clip(RoundedCornerShape(50))
                    .background(Hd.Accent.copy(alpha = 0.12f))
                    .clickable {
                        if (unreachable) vm.refresh(force = true) else vm.startEngine()
                    }
                    .padding(horizontal = 12.dp, vertical = 6.dp),
            )
        }
        vm.engineError?.let { error ->
            Text(
                error,
                style = MaterialTheme.typography.labelSmall,
                color = Hd.Bad,
                modifier = Modifier.padding(top = 6.dp),
            )
        }
    }
}
