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
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import net.syslikoff.healdragg.BuildConfig

@Composable
fun SettingsScreen(
    vm: AppViewModel,
    onOpenBattery: () -> Unit,
    onToast: (String) -> Unit,
    onEditServer: () -> Unit,
    onOpenAccounts: () -> Unit,
) {
    var logOpen by remember { mutableStateOf(false) }
    var apiId by remember(vm.apiId) { mutableStateOf(vm.apiId) }
    var apiHash by remember(vm.apiHash) { mutableStateOf(vm.apiHash) }
    var url by remember(vm.baseUrl) { mutableStateOf(vm.baseUrl) }
    var token by remember(vm.token) { mutableStateOf(vm.token) }
    var probeResult by remember { mutableStateOf<String?>(null) }
    var probing by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 6.dp, bottom = 30.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item("engine") {
            HdCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    EngineStateBadge(vm.engine)
                    Spacer(Modifier.width(10.dp))
                    Column(Modifier.weight(1f)) {
                        Text("Движок", style = MaterialTheme.typography.titleSmall, color = Hd.Text)
                        Text(
                            text = when (vm.engine) {
                                EngineState.Ready -> "работает на 127.0.0.1:8080"
                                EngineState.Starting -> "запускается…"
                                EngineState.Stopped -> "остановлен"
                                EngineState.Error -> "не запустился"
                                EngineState.Unknown -> "адрес: ${vm.baseUrl}"
                            },
                            style = MaterialTheme.typography.bodySmall,
                            color = Hd.Muted,
                        )
                    }
                }
                vm.engineError?.let { error ->
                    Spacer(Modifier.height(8.dp))
                    Text(error, color = Hd.Bad, style = MaterialTheme.typography.labelSmall)
                }
                Spacer(Modifier.height(12.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (vm.engine == EngineState.Ready) {
                        OutlinedButton(
                            onClick = { vm.stopEngine() },
                            shape = RoundedCornerShape(12.dp),
                            modifier = Modifier.weight(1f).heightIn(min = 44.dp),
                        ) {
                            Text("Остановить", color = Hd.Muted, style = MaterialTheme.typography.labelLarge)
                        }
                        OutlinedButton(
                            onClick = { vm.restartEngine() },
                            shape = RoundedCornerShape(12.dp),
                            modifier = Modifier.weight(1f).heightIn(min = 44.dp),
                        ) {
                            Text("Перезапустить", color = Hd.Accent2, style = MaterialTheme.typography.labelLarge)
                        }
                    } else {
                        Button(
                            onClick = { vm.startEngine() },
                            enabled = !vm.engineBusy,
                            shape = RoundedCornerShape(12.dp),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = Hd.Accent,
                                contentColor = Hd.Black,
                            ),
                            modifier = Modifier.weight(1f).heightIn(min = 46.dp),
                        ) {
                            if (vm.engineBusy) {
                                CircularProgressIndicator(
                                    strokeWidth = 2.dp,
                                    color = Hd.Black,
                                    modifier = Modifier.size(16.dp),
                                )
                                Spacer(Modifier.width(8.dp))
                            }
                            Text("Запустить", fontWeight = FontWeight.SemiBold)
                        }
                    }
                }
                Spacer(Modifier.height(6.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    TextButton(onClick = { logOpen = true }) {
                        Text("Лог движка", color = Hd.Accent2, style = MaterialTheme.typography.labelLarge)
                    }
                    TextButton(onClick = onOpenBattery) {
                        Text(
                            if (vm.batteryOptimized()) "🔋 батарея: без ограничений" else "🔋 разрешить фон",
                            color = if (vm.batteryOptimized()) Hd.Good else Hd.Warn,
                            style = MaterialTheme.typography.labelLarge,
                        )
                    }
                }
            }
        }

        item("accounts") {
            HdCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "Аккаунты-сканеры",
                        style = MaterialTheme.typography.titleSmall,
                        color = Hd.Text,
                        modifier = Modifier.weight(1f),
                    )
                    Text(
                        vm.sessions.size.toString(),
                        style = MaterialTheme.typography.labelMedium,
                        color = if (vm.sessions.isEmpty()) Hd.Warn else Hd.Good,
                    )
                }
                Spacer(Modifier.height(4.dp))
                Text(
                    if (vm.sessions.isEmpty()) {
                        "Ни один аккаунт не подключён — скан и добавление цели не запустятся."
                    } else {
                        vm.sessions.joinToString(" · ") { session ->
                            session.username?.let { "@$it" } ?: session.name
                        }
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = if (vm.sessions.isEmpty()) Hd.Warn else Hd.Muted,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(10.dp))
                OutlinedButton(
                    onClick = { onOpenAccounts() },
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.fillMaxWidth().heightIn(min = 44.dp),
                ) {
                    Text(
                        if (vm.sessions.isEmpty()) "Войти в Telegram" else "Управлять аккаунтами",
                        color = Hd.Accent2,
                        style = MaterialTheme.typography.labelLarge,
                    )
                }
            }
        }

        item("telegram") {
            HdCard {
                Text("Ключи Telegram", style = MaterialTheme.typography.titleSmall, color = Hd.Text)
                Spacer(Modifier.height(4.dp))
                Text(
                    "my.telegram.org → API development tools. Ключи остаются только на этом устройстве.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Hd.Muted,
                )
                Spacer(Modifier.height(10.dp))
                HdTextField(
                    value = apiId,
                    onValueChange = { apiId = it.filter(Char::isDigit) },
                    label = "API_ID",
                    keyboard = androidx.compose.ui.text.input.KeyboardType.Number,
                )
                Spacer(Modifier.height(8.dp))
                HdTextField(
                    value = apiHash,
                    onValueChange = { apiHash = it.trim() },
                    label = "API_HASH",
                    isError = apiHash.isNotBlank() && apiHash.length < 16,
                )
                Spacer(Modifier.height(10.dp))
                Button(
                    onClick = {
                        val changed = apiId.trim() != vm.apiId || apiHash.trim() != vm.apiHash
                        vm.saveCredentials(apiId, apiHash)
                        when {
                            !changed -> onToast("Ключи без изменений")
                            vm.engine == EngineState.Ready -> {
                                vm.restartEngine()
                                onToast("Ключи сохранены — движок перезапускается")
                            }
                            vm.isLocal -> {
                                vm.startEngine()
                                onToast("Ключи сохранены — запускаю движок")
                            }
                            else -> onToast("Ключи сохранены")
                        }
                    },
                    enabled = apiId.isNotBlank() && apiHash.length >= 16,
                    shape = RoundedCornerShape(12.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Hd.Accent, contentColor = Hd.Black),
                    modifier = Modifier.heightIn(min = 44.dp),
                ) { Text("Сохранить ключи") }
            }
        }

        item("server") {
            HdCard {
                Text("Подключение", style = MaterialTheme.typography.titleSmall, color = Hd.Text)
                Spacer(Modifier.height(4.dp))
                Text(
                    if (vm.isLocal) "Сейчас используется встроенный движок."
                    else "Сейчас используется внешний сервер.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Hd.Muted,
                )
                Spacer(Modifier.height(10.dp))
                HdTextField(value = url, onValueChange = { url = it }, label = "Адрес панели")
                Spacer(Modifier.height(8.dp))
                HdTextField(value = token, onValueChange = { token = it }, label = "API_TOKEN")
                Spacer(Modifier.height(10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(
                        onClick = {
                            scope.launch {
                                probing = true
                                probeResult = null
                                vm.saveServer(url, token)
                                kotlinx.coroutines.delay(400)
                                probeResult = if (vm.api?.health() == true) "Связь есть ✓" else "Нет связи ✗"
                                probing = false
                            }
                        },
                        enabled = !probing,
                        shape = RoundedCornerShape(12.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = Hd.Accent, contentColor = Hd.Black),
                        modifier = Modifier.heightIn(min = 44.dp),
                    ) { Text(if (probing) "Проверяю…" else "Применить") }
                    OutlinedButton(
                        onClick = { onEditServer() },
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.heightIn(min = 44.dp),
                    ) { Text("Мастер настройки", color = Hd.Accent2) }
                }
                probeResult?.let {
                    Spacer(Modifier.height(8.dp))
                    Text(
                        it,
                        color = if (it.startsWith("Связь есть")) Hd.Good else Hd.Bad,
                        style = MaterialTheme.typography.labelMedium,
                    )
                }
            }
        }

        item("about") {
            HdCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.Info, contentDescription = null, tint = Hd.Accent2)
                    Spacer(Modifier.width(8.dp))
                    Text("О приложении", style = MaterialTheme.typography.titleSmall, color = Hd.Text)
                }
                Spacer(Modifier.height(8.dp))
                KeyValueRow("Версия", "${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})")
                KeyValueRow("Движок", "CPython ${'3'}.13 · Chaquopy")
                KeyValueRow("Данные", if (vm.isLocal) "только на устройстве" else vm.baseUrl)
                Spacer(Modifier.height(8.dp))
                Text(
                    "Монитор публичных чатов: метаданные и сообщения цели. Без скрытого сбора, " +
                        "без обхода приватности и без чужих аккаунтов.",
                    style = MaterialTheme.typography.labelSmall,
                    color = Hd.Dim,
                )
            }
        }
    }

    if (logOpen) {
        EngineLogDialog(text = vm.engineLog(), onDismiss = { logOpen = false }, onToast = onToast)
    }
}

@Composable
fun EngineStateBadge(state: EngineState) {
    val color = when (state) {
        EngineState.Ready -> Hd.Good
        EngineState.Starting -> Hd.Warn
        EngineState.Error -> Hd.Bad
        else -> Hd.Dim
    }
    Box(
        Modifier
            .size(12.dp)
            .background(color, CircleShape),
    )
}

@Composable
fun EngineLogDialog(text: String, onDismiss: () -> Unit, onToast: (String) -> Unit) {
    val context = LocalContext.current
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Лог движка") },
        text = {
            Box(
                Modifier
                    .heightIn(max = 420.dp)
                    .verticalScroll(rememberScrollState()),
            ) {
                Text(
                    text,
                    fontFamily = FontFamily.Monospace,
                    fontSize = 11.sp,
                    color = Hd.Text,
                )
            }
        },
        confirmButton = {
            TextButton(onClick = {
                copyText(context, "healdragg log", text)
                onToast("Лог скопирован")
            }) { Text("Копировать", color = Hd.Accent2) }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Закрыть", color = Hd.Muted) }
        },
        containerColor = Hd.Surface,
        titleContentColor = Hd.Text,
        textContentColor = Hd.Text,
    )
}

// ── мастер первого запуска ──────────────────────────────────────────────────

@Composable
fun SetupScreen(
    vm: AppViewModel,
    onToast: (String) -> Unit,
    onOpenBattery: () -> Unit,
    onDone: () -> Unit,
    onOpenAccounts: () -> Unit = {},
) {
    var apiId by remember { mutableStateOf(vm.apiId) }
    var apiHash by remember { mutableStateOf(vm.apiHash) }
    var remote by remember { mutableStateOf(!vm.isLocal) }
    var url by remember { mutableStateOf(if (vm.isLocal) "" else vm.baseUrl) }
    var token by remember { mutableStateOf(vm.token) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Hd.Bg)
            .statusBarsPadding()
            .navigationBarsPadding()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 22.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Spacer(Modifier.height(12.dp))
        Box(
            Modifier
                .size(76.dp)
                .background(Hd.Accent.copy(alpha = 0.16f), RoundedCornerShape(24.dp)),
            contentAlignment = Alignment.Center,
        ) { Text("🩺", fontSize = 34.sp) }
        Spacer(Modifier.height(12.dp))
        Text("Healdragg", style = MaterialTheme.typography.displaySmall, color = Hd.Text)
        Text(
            "Монитор публичных чатов Telegram.\nДвижок уже внутри приложения.",
            style = MaterialTheme.typography.bodySmall,
            color = Hd.Muted,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
        )
        Spacer(Modifier.height(16.dp))
        EngineStateRow(vm)

        if (!remote) {
            Spacer(Modifier.height(14.dp))
            HdCard {
                Text("Ключи Telegram", style = MaterialTheme.typography.titleSmall, color = Hd.Text)
                Spacer(Modifier.height(4.dp))
                Text(
                    "1. Открой my.telegram.org → API development tools\n" +
                        "2. Создай приложение и скопируй API_ID и API_HASH",
                    style = MaterialTheme.typography.bodySmall,
                    color = Hd.Muted,
                )
                Spacer(Modifier.height(10.dp))
                HdTextField(
                    value = apiId,
                    onValueChange = { apiId = it.filter(Char::isDigit); error = null },
                    label = "API_ID",
                    keyboard = androidx.compose.ui.text.input.KeyboardType.Number,
                    isError = error != null,
                )
                Spacer(Modifier.height(8.dp))
                HdTextField(
                    value = apiHash,
                    onValueChange = { apiHash = it.trim(); error = null },
                    label = "API_HASH",
                    isError = error != null,
                )
                error?.let {
                    Spacer(Modifier.height(8.dp))
                    Text(it, color = Hd.Bad, style = MaterialTheme.typography.bodySmall)
                }
                Spacer(Modifier.height(12.dp))
                Button(
                    onClick = {
                        if (apiId.isBlank() || apiHash.length < 16) {
                            error = "Нужны оба ключа: API_ID (число) и API_HASH (32 символа)"
                            return@Button
                        }
                        vm.saveCredentials(apiId, apiHash)
                        vm.startEngine()
                    },
                    enabled = !vm.engineBusy,
                    shape = RoundedCornerShape(14.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Hd.Accent, contentColor = Hd.Black),
                    modifier = Modifier.fillMaxWidth().heightIn(min = 50.dp),
                ) {
                    if (vm.engineBusy) {
                        CircularProgressIndicator(strokeWidth = 2.dp, color = Hd.Black, modifier = Modifier.size(16.dp))
                        Spacer(Modifier.width(8.dp))
                    } else {
                        Icon(Icons.Filled.PlayArrow, contentDescription = null)
                        Spacer(Modifier.width(6.dp))
                    }
                    Text("Запустить движок", fontWeight = FontWeight.SemiBold)
                }
                Spacer(Modifier.height(6.dp))
                TextButton(onClick = onOpenBattery) {
                    Text("🔋 Разрешить работу в фоне", color = Hd.Muted, style = MaterialTheme.typography.labelLarge)
                }
            }
        }

        if (vm.engine == EngineState.Ready) {
            Spacer(Modifier.height(12.dp))
            HdCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("✅", fontSize = 18.sp)
                    Spacer(Modifier.width(8.dp))
                    Text(
                        "Движок работает",
                        style = MaterialTheme.typography.titleSmall,
                        color = Hd.Text,
                        modifier = Modifier.weight(1f),
                    )
                }
                Spacer(Modifier.height(6.dp))
                Text(
                    if (vm.sessions.isEmpty()) {
                        "Остался один шаг: подключи Telegram-аккаунт, с которого будем читать чаты."
                    } else {
                        "Аккаунт подключён. Можно добавлять цели и запускать скан."
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = Hd.Muted,
                )
                Spacer(Modifier.height(10.dp))
                if (vm.sessions.isEmpty()) {
                    Button(
                        onClick = { onOpenAccounts() },
                        shape = RoundedCornerShape(12.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = Hd.Accent, contentColor = Hd.Black),
                        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                    ) { Text("Войти в Telegram", fontWeight = FontWeight.SemiBold) }
                }
                OutlinedButton(
                    onClick = { onDone() },
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.fillMaxWidth().heightIn(min = 44.dp),
                ) { Text(if (vm.sessions.isEmpty()) "Позже" else "Перейти к целям", color = Hd.Accent2) }
            }
        }

        Spacer(Modifier.height(12.dp))
        HdCard {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Свой сервер", style = MaterialTheme.typography.titleSmall, color = Hd.Text, modifier = Modifier.weight(1f))
                TextButton(onClick = { remote = !remote }) {
                    Text(if (remote) "Скрыть" else "Настроить", color = Hd.Accent2, style = MaterialTheme.typography.labelLarge)
                }
            }
            if (remote) {
                Text(
                    "Если панель уже работает на компьютере или VPS — укажи адрес и токен.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Hd.Muted,
                )
                Spacer(Modifier.height(8.dp))
                HdTextField(value = url, onValueChange = { url = it }, label = "Адрес", hint = "http://192.168.1.10:8080")
                Spacer(Modifier.height(8.dp))
                HdTextField(value = token, onValueChange = { token = it }, label = "API_TOKEN")
                Spacer(Modifier.height(10.dp))
                Button(
                    onClick = {
                        vm.saveServer(url, token)
                        scope.launch {
                            kotlinx.coroutines.delay(600)
                            if (vm.api?.health() == true) {
                                onToast("Связь есть")
                                onDone()
                            } else {
                                error = "Сервер не ответил — проверь адрес и токен"
                            }
                        }
                    },
                    shape = RoundedCornerShape(12.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = Hd.Accent2, contentColor = Hd.Black),
                    modifier = Modifier.fillMaxWidth().heightIn(min = 46.dp),
                ) { Text("Подключиться", fontWeight = FontWeight.SemiBold) }
            }
        }

        Spacer(Modifier.height(14.dp))
        Text(
            "Ключи и база остаются на устройстве. Приложение не отправляет их никуда, кроме Telegram.",
            style = MaterialTheme.typography.labelSmall,
            color = Hd.Dim,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
        )
    }
}

@Composable
private fun EngineStateRow(vm: AppViewModel) {
    Row(
        modifier = Modifier
            .background(Hd.Surface, RoundedCornerShape(50))
            .padding(horizontal = 12.dp, vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        EngineStateBadge(vm.engine)
        Spacer(Modifier.width(8.dp))
        Text(
            when (vm.engine) {
                EngineState.Ready -> "движок работает"
                EngineState.Starting -> "движок запускается…"
                EngineState.Stopped -> "движок остановлен"
                EngineState.Error -> "движок не запустился"
                EngineState.Unknown -> "движок не проверен"
            },
            style = MaterialTheme.typography.labelMedium,
            color = Hd.Muted,
        )
    }
}
