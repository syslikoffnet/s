package net.syslikoff.healdragg.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch

private enum class Tab(val title: String, val emoji: String) {
    Targets("Цели", "🎯"),
    Alerts("Алерты", "🔔"),
    Settings("Настройки", "⚙"),
}

/**
 * Корень приложения: адаптивный каркас, вкладки, шторка движка и навигация.
 * На телефоне — нижняя панель и push-переходы, на планшете — rail и две колонки.
 */
@Composable
fun HealdraggApp(vm: AppViewModel, onOpenBattery: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    val toast: (String) -> Unit = { message ->
        scope.launch { snackbar.showSnackbar(message) }
    }

    var tab by rememberSaveable { mutableStateOf(Tab.Targets) }
    var openTarget by rememberSaveable { mutableStateOf<Long?>(null) }
    var showSetup by rememberSaveable {
        mutableStateOf(vm.isLocal && !vm.hasCredentials)
    }

    BackHandler(enabled = openTarget != null) { openTarget = null }
    BackHandler(enabled = openTarget == null && showSetup && !vm.isLocal) { showSetup = false }

    if (showSetup) {
        SetupScreen(
            vm = vm,
            onToast = toast,
            onOpenBattery = onOpenBattery,
            onDone = {
                showSetup = false
                vm.refresh(force = true)
            },
        )
        SnackbarHost(hostState = snackbar)
        return
    }

    BoxWithConstraints(Modifier.fillMaxSize().background(Hd.Bg)) {
        val wide = maxWidth >= 760.dp

        if (wide) {
            Row(Modifier.fillMaxSize()) {
                NavigationRail(
                    containerColor = Hd.Surface,
                    header = { RailHeader(vm) },
                ) {
                    Spacer(Modifier.height(12.dp))
                    Tab.entries.forEach { item ->
                        NavigationRailItem(
                            selected = tab == item && openTarget == null,
                            onClick = {
                                openTarget = null
                                tab = item
                            },
                            icon = { TabIcon(item, vm) },
                            label = {
                                Text(
                                    item.title,
                                    style = MaterialTheme.typography.labelMedium,
                                )
                            },
                            colors = railColors(),
                        )
                    }
                }
                Column(Modifier.weight(1f).fillMaxSize()) {
                    HdTopBar(
                        vm = vm,
                        title = when {
                            openTarget != null -> "Цель"
                            else -> tab.title
                        },
                        onRefresh = { vm.refresh(force = true) },
                        onSettings = { showSetup = true },
                    )
                    Box(Modifier.weight(1f)) {
                        TabContent(
                            vm = vm,
                            tab = tab,
                            openTarget = openTarget,
                            onOpenTarget = { openTarget = it },
                            onCloseTarget = { openTarget = null },
                            onToast = toast,
                            onOpenBattery = onOpenBattery,
                            onBackToSetup = { showSetup = true },
                        )
                    }
                }
            }
        } else {
            Scaffold(
                containerColor = Hd.Bg,
                contentWindowInsets = androidx.compose.foundation.layout.WindowInsets(0, 0, 0, 0),
                snackbarHost = { SnackbarHost(snackbar) },
                topBar = {
                    HdTopBar(
                        vm = vm,
                        title = if (openTarget != null) "Цель" else tab.title,
                        onRefresh = { vm.refresh(force = true) },
                        onSettings = { showSetup = true },
                    )
                },
                bottomBar = {
                    if (openTarget == null) {
                        NavigationBar(containerColor = Hd.Surface) {
                            Tab.entries.forEach { item ->
                                NavigationBarItem(
                                    selected = tab == item,
                                    onClick = { tab = item },
                                    icon = { TabIcon(item, vm) },
                                    label = {
                                        Text(item.title, style = MaterialTheme.typography.labelMedium)
                                    },
                                    colors = NavigationBarItemDefaults.colors(
                                        selectedIconColor = Hd.Accent,
                                        selectedTextColor = Hd.Accent,
                                        indicatorColor = Hd.Accent.copy(alpha = 0.16f),
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
                    TabContent(
                        vm = vm,
                        tab = tab,
                        openTarget = openTarget,
                        onOpenTarget = { openTarget = it },
                        onCloseTarget = { openTarget = null },
                        onToast = toast,
                        onOpenBattery = onOpenBattery,
                        onBackToSetup = { showSetup = true },
                    )
                }
            }
        }
    }
    SnackbarHost(hostState = snackbar, modifier = Modifier.navigationBarsPadding())
}

@Composable
private fun TabContent(
    vm: AppViewModel,
    tab: Tab,
    openTarget: Long?,
    onOpenTarget: (Long) -> Unit,
    onCloseTarget: () -> Unit,
    onToast: (String) -> Unit,
    onOpenBattery: () -> Unit,
    onBackToSetup: () -> Unit,
) {
    Crossfade(targetState = openTarget to tab, label = "screen") { (target, currentTab) ->
        when {
            target != null -> TargetDetailScreen(
                vm = vm,
                tgId = target,
                onBack = onCloseTarget,
                onToast = onToast,
            )
            currentTab == Tab.Targets -> TargetsScreen(
                vm = vm,
                onOpenTarget = onOpenTarget,
                onToast = onToast,
            )
            currentTab == Tab.Alerts -> AlertsScreen(
                vm = vm,
                onOpenTarget = onOpenTarget,
                onToast = onToast,
            )
            else -> SettingsScreen(
                vm = vm,
                onOpenBattery = onOpenBattery,
                onToast = onToast,
                onEditServer = onBackToSetup,
            )
        }
    }
}

@Composable
private fun RailHeader(vm: AppViewModel) {
    Column(
        Modifier.statusBarsPadding().padding(top = 12.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(
            Modifier
                .size(42.dp)
                .background(Hd.Accent.copy(alpha = 0.16f), RoundedCornerShape(14.dp)),
            contentAlignment = Alignment.Center,
        ) { Text("🩺", fontSize = 20.sp) }
        Spacer(Modifier.height(6.dp))
        EngineDot(vm.engine)
    }
}

@Composable
private fun TabIcon(item: Tab, vm: AppViewModel) {
    if (item == Tab.Alerts && vm.unread > 0) {
        BadgedBox(badge = {
            Badge(containerColor = Hd.Bad, contentColor = Hd.Text) {
                Text(if (vm.unread > 99) "99+" else vm.unread.toString(), fontSize = 10.sp)
            }
        }) {
            Text(item.emoji, fontSize = 19.sp)
        }
    } else {
        Text(item.emoji, fontSize = 19.sp)
    }
}

@Composable
private fun railColors() = androidx.compose.material3.NavigationRailItemDefaults.colors(
    selectedIconColor = Hd.Accent,
    selectedTextColor = Hd.Accent,
    indicatorColor = Hd.Accent.copy(alpha = 0.16f),
    unselectedIconColor = Hd.Muted,
    unselectedTextColor = Hd.Muted,
)

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
            .padding(start = 18.dp, end = 10.dp, top = 8.dp, bottom = 6.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = title,
                style = MaterialTheme.typography.headlineSmall,
                color = Hd.Text,
                modifier = Modifier.weight(1f),
            )
            EngineDot(vm.engine)
            Spacer(Modifier.width(6.dp))
            IconButton(onClick = onRefresh, enabled = !vm.refreshing) {
                Icon(
                    Icons.Filled.Refresh,
                    contentDescription = "Обновить данные",
                    tint = if (vm.refreshing) Hd.Dim else Hd.Muted,
                )
            }
            IconButton(onClick = onSettings) {
                Icon(
                    Icons.Filled.Settings,
                    contentDescription = "Настройки",
                    tint = Hd.Muted,
                )
            }
        }
        if (vm.offline && vm.engine != EngineState.Ready) {
            Spacer(Modifier.height(4.dp))
            EngineBanner(vm, onRefresh)
        }
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
            .size(10.dp)
            .background(color.copy(alpha = if (state == EngineState.Starting) alpha else 1f), CircleShape)
            .semantics { contentDescription = desc },
    )
}

@Composable
private fun EngineBanner(vm: AppViewModel, onRetry: () -> Unit) {
    HdCard(contentPadding = androidx.compose.foundation.layout.PaddingValues(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                when (vm.engine) {
                    EngineState.Starting -> "⏳ Движок запускается…"
                    EngineState.Error -> "⚠️ Движок не запустился"
                    EngineState.Stopped -> "⚪ Движок остановлен"
                    else -> "⚠️ Нет связи с движком"
                },
                style = MaterialTheme.typography.bodySmall,
                color = Hd.Muted,
                modifier = Modifier.weight(1f),
            )
            Text(
                text = "Повторить",
                color = Hd.Accent2,
                style = MaterialTheme.typography.labelLarge,
                modifier = Modifier
                    .padding(start = 8.dp)
                    .semantics { contentDescription = "Перезапустить движок" }
                    .then(Modifier)
                    .padding(6.dp),
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
