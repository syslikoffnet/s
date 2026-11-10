package net.syslikoff.healdragg.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Lock
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material.icons.filled.UploadFile
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

private enum class LoginStep { Idle, Phone, Code, Password, Done }

/**
 * Аккаунты-сканеры: список подключённых Telegram-аккаунтов и вход по номеру,
 * коду и паролю 2FA. Раньше для этого требовался терминал — теперь всё здесь.
 */
@Composable
fun AccountsScreen(vm: AppViewModel, onBack: () -> Unit, onToast: (String) -> Unit) {
    var step by rememberSaveable { mutableStateOf(LoginStep.Idle) }
    var phone by rememberSaveable { mutableStateOf("") }
    var code by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var flowId by rememberSaveable { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var doneHandle by remember { mutableStateOf<String?>(null) }
    var toDelete by remember { mutableStateOf<SessionInfo?>(null) }
    var importing by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val pickSession = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        val payload = readDocument(context, uri)
        if (payload == null) {
            onToast("Не удалось прочитать файл")
            return@rememberLauncherForActivityResult
        }
        val name = fileNameFrom(context, uri)
        importing = true
        error = null
        vm.importSession(name, payload) { result ->
            importing = false
            result.onSuccess { outcome ->
                onToast(outcome.summary)
                if (outcome.imported.isNotEmpty()) vm.refreshSessions()
                if (outcome.imported.isEmpty() && outcome.errors.isNotEmpty()) {
                    error = outcome.errors.first().error
                }
            }.onFailure { error = it.message ?: "Не удалось импортировать аккаунт" }
        }
    }

    fun reset() {
        flowId?.let(vm::cancelLogin)
        step = LoginStep.Idle
        phone = ""
        code = ""
        password = ""
        flowId = null
        error = null
        busy = false
    }

    fun sendCode() {
        if (busy) return
        busy = true
        error = null
        vm.startLogin(phone.trim()) { result ->
            busy = false
            result.onSuccess { flow ->
                flowId = flow.id
                code = ""
                step = LoginStep.Code
                onToast("Код отправлен в Telegram")
            }.onFailure { error = it.message ?: "Не удалось отправить код" }
        }
    }

    fun resend() {
        if (busy) return
        flowId?.let(vm::cancelLogin)
        flowId = null
        sendCode()
    }

    fun confirmCode() {
        val id = flowId ?: return
        if (busy) return
        busy = true
        error = null
        vm.submitLoginCode(id, code) { result ->
            busy = false
            result.onSuccess { flow ->
                if (flow.passwordNeeded) {
                    password = ""
                    step = LoginStep.Password
                } else if (flow.done) {
                    doneHandle = flow.username?.let { "@$it" } ?: flow.name
                    step = LoginStep.Done
                    flowId = null
                    onToast("Аккаунт подключён")
                }
            }.onFailure { error = it.message ?: "Код не подошёл" }
        }
    }

    fun confirmPassword() {
        val id = flowId ?: return
        if (busy) return
        busy = true
        error = null
        vm.submitLoginPassword(id, password) { result ->
            busy = false
            result.onSuccess { flow ->
                doneHandle = flow.username?.let { "@$it" } ?: flow.name
                step = LoginStep.Done
                flowId = null
                onToast("Аккаунт подключён")
            }.onFailure { error = it.message ?: "Пароль не подошёл" }
        }
    }

    val activeFlow = rememberUpdatedState(flowId)
    DisposableEffect(Unit) {
        onDispose { activeFlow.value?.let(vm::cancelLogin) }
    }

    Column(
        Modifier
            .fillMaxSize()
            .background(Hd.Bg)
            .statusBarsPadding()
            .navigationBarsPadding()
            .imePadding(),
    ) {
        Row(
            Modifier.fillMaxWidth().padding(start = 4.dp, end = 12.dp, top = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onBack) {
                Icon(Icons.Filled.ArrowBack, contentDescription = "Назад", tint = Hd.Muted)
            }
            Text(
                "Аккаунты",
                style = MaterialTheme.typography.headlineSmall,
                color = Hd.Text,
                modifier = Modifier.weight(1f),
            )
            if (busy) {
                CircularProgressIndicator(
                    modifier = Modifier.size(18.dp),
                    strokeWidth = 2.dp,
                    color = Hd.Accent,
                )
            }
        }

        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 6.dp, bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item("intro") {
                HdCard {
                    Text(
                        "Зачем аккаунт",
                        style = MaterialTheme.typography.titleSmall,
                        color = Hd.Text,
                    )
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "Приложение читает открытые чаты цели от имени твоего Telegram. " +
                            "Номер и код обрабатывает движок на этом телефоне — они не уходят на сторонние серверы.",
                        style = MaterialTheme.typography.bodySmall,
                        color = Hd.Muted,
                    )
                }
            }

            item("import") {
                HdCard {
                    Text(
                        "Импорт готового аккаунта",
                        style = MaterialTheme.typography.titleSmall,
                        color = Hd.Text,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "Уже есть сессия? Загрузите файл .session (Telethon), .json (StringSession) " +
                            "или архив tdata из Telegram Desktop — вход по коду не понадобится. " +
                            "Аккаунт сохранится в приложении и попадёт в резервную копию.",
                        style = MaterialTheme.typography.bodySmall,
                        color = Hd.Muted,
                    )
                    Spacer(Modifier.height(10.dp))
                    OutlinedButton(
                        onClick = {
                            runCatching {
                                pickSession.launch(
                                    arrayOf(
                                        "application/octet-stream",
                                        "application/zip",
                                        "application/json",
                                        "*/*",
                                    )
                                )
                            }.onFailure { onToast("Не удалось открыть выбор файла") }
                        },
                        enabled = !importing,
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.fillMaxWidth().heightIn(min = 46.dp),
                    ) {
                        if (importing) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(16.dp),
                                strokeWidth = 2.dp,
                                color = Hd.Accent2,
                            )
                            Spacer(Modifier.width(8.dp))
                        } else {
                            Icon(
                                Icons.Filled.UploadFile,
                                contentDescription = null,
                                tint = Hd.Accent2,
                                modifier = Modifier.size(18.dp),
                            )
                            Spacer(Modifier.width(8.dp))
                        }
                        Text(
                            if (importing) "Импортирую…" else "Загрузить .session / tdata.zip",
                            color = Hd.Accent2,
                            style = MaterialTheme.typography.labelLarge,
                        )
                    }
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "tdata: заархивируйте папку tdata целиком в zip (внутри должен быть key_datas). " +
                            "Аккаунт с паролем на tdata нужно сначала разархивировать в Telegram Desktop.",
                        style = MaterialTheme.typography.labelSmall,
                        color = Hd.Dim,
                    )
                }
            }

            item("sessions-label") {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    SectionLabel("Подключённые аккаунты")
                    Spacer(Modifier.width(8.dp))
                    Text(
                        vm.sessions.size.toString(),
                        style = MaterialTheme.typography.labelMedium,
                        color = Hd.Dim,
                        modifier = Modifier.weight(1f),
                    )
                    TextButton(onClick = { vm.refreshSessions() }) {
                        Text("Обновить", color = Hd.Accent2, style = MaterialTheme.typography.labelMedium)
                    }
                }
            }

            if (vm.sessions.isEmpty()) {
                item("sessions-empty") {
                    EmptyState(
                        emoji = "",
                        icon = Icons.Filled.Lock,
                        title = "Аккаунт не подключён",
                        text = "Без него приложение не сможет искать чаты. Вход занимает минуту.",
                        actionLabel = if (step == LoginStep.Idle) "Войти в Telegram" else null,
                        onAction = if (step == LoginStep.Idle) ({ step = LoginStep.Phone }) else null,
                    )
                }
            } else {
                items(vm.sessions, key = { it.name }) { session ->
                    SessionRow(session) { toDelete = session }
                }
            }

            if (step != LoginStep.Idle) {
                item("login") {
                    HdCard {
                        when (step) {
                            LoginStep.Phone -> {
                                Text(
                                    "Вход в Telegram",
                                    style = MaterialTheme.typography.titleSmall,
                                    color = Hd.Text,
                                )
                                Spacer(Modifier.height(4.dp))
                                Text(
                                    "Код придёт в приложение Telegram, а не по SMS.",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = Hd.Muted,
                                )
                                Spacer(Modifier.height(12.dp))
                                HdTextField(
                                    value = phone,
                                    onValueChange = { phone = it.take(20) },
                                    label = "Номер телефона",
                                    hint = "+7 999 000-00-00",
                                    keyboard = KeyboardType.Phone,
                                    isError = error != null,
                                )
                                Spacer(Modifier.height(12.dp))
                                Button(
                                    onClick = { sendCode() },
                                    enabled = !busy && phone.count { it.isDigit() } >= 6,
                                    shape = RoundedCornerShape(12.dp),
                                    colors = ButtonDefaults.buttonColors(
                                        containerColor = Hd.Accent,
                                        contentColor = Hd.Black,
                                    ),
                                    modifier = Modifier.fillMaxWidth().heightIn(min = 46.dp),
                                ) {
                                    Text("Получить код", fontWeight = FontWeight.SemiBold)
                                }
                                TextButton(
                                    onClick = { reset() },
                                    modifier = Modifier.fillMaxWidth(),
                                ) {
                                    Text("Отмена", color = Hd.Muted, style = MaterialTheme.typography.labelMedium)
                                }
                            }

                            LoginStep.Code -> {
                                Text(
                                    "Код из Telegram",
                                    style = MaterialTheme.typography.titleSmall,
                                    color = Hd.Text,
                                )
                                Spacer(Modifier.height(4.dp))
                                Text(
                                    "Отправили на ${phone.trim()}. Введи 5–6 цифр из сообщения.",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = Hd.Muted,
                                )
                                Spacer(Modifier.height(12.dp))
                                HdTextField(
                                    value = code,
                                    onValueChange = { value ->
                                        code = value.filter { it.isDigit() }.take(6)
                                    },
                                    label = "Код",
                                    hint = "00000",
                                    keyboard = KeyboardType.NumberPassword,
                                    isError = error != null,
                                )
                                Spacer(Modifier.height(12.dp))
                                Button(
                                    onClick = { confirmCode() },
                                    enabled = !busy && code.length >= 4,
                                    shape = RoundedCornerShape(12.dp),
                                    colors = ButtonDefaults.buttonColors(
                                        containerColor = Hd.Accent,
                                        contentColor = Hd.Black,
                                    ),
                                    modifier = Modifier.fillMaxWidth().heightIn(min = 46.dp),
                                ) {
                                    Text("Войти", fontWeight = FontWeight.SemiBold)
                                }
                                Row(Modifier.fillMaxWidth()) {
                                    TextButton(onClick = { resend() }, enabled = !busy) {
                                        Text(
                                            "Ещё раз",
                                            color = if (busy) Hd.Dim else Hd.Accent2,
                                            style = MaterialTheme.typography.labelMedium,
                                        )
                                    }
                                    Spacer(Modifier.weight(1f))
                                    TextButton(onClick = { reset() }) {
                                        Text("Отмена", color = Hd.Muted, style = MaterialTheme.typography.labelMedium)
                                    }
                                }
                            }

                            LoginStep.Password -> {
                                Text(
                                    "Двухэтапная проверка",
                                    style = MaterialTheme.typography.titleSmall,
                                    color = Hd.Text,
                                )
                                Spacer(Modifier.height(4.dp))
                                Text(
                                    "На аккаунте включён облачный пароль. Введи его — он проверяется в Telegram.",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = Hd.Muted,
                                )
                                Spacer(Modifier.height(12.dp))
                                HdTextField(
                                    value = password,
                                    onValueChange = { password = it },
                                    label = "Пароль 2FA",
                                    keyboard = KeyboardType.Password,
                                    isError = error != null,
                                    visualTransformation = PasswordVisualTransformation(),
                                )
                                Spacer(Modifier.height(12.dp))
                                Button(
                                    onClick = { confirmPassword() },
                                    enabled = !busy && password.isNotBlank(),
                                    shape = RoundedCornerShape(12.dp),
                                    colors = ButtonDefaults.buttonColors(
                                        containerColor = Hd.Accent,
                                        contentColor = Hd.Black,
                                    ),
                                    modifier = Modifier.fillMaxWidth().heightIn(min = 46.dp),
                                ) {
                                    Text("Войти", fontWeight = FontWeight.SemiBold)
                                }
                                TextButton(onClick = { reset() }, modifier = Modifier.fillMaxWidth()) {
                                    Text("Отмена", color = Hd.Muted, style = MaterialTheme.typography.labelMedium)
                                }
                            }

                            LoginStep.Done -> {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Icon(
                                        Icons.Filled.CheckCircle,
                                        contentDescription = null,
                                        tint = Hd.Good,
                                        modifier = Modifier.size(26.dp),
                                    )
                                    Spacer(Modifier.width(10.dp))
                                    Column(Modifier.weight(1f)) {
                                        Text(
                                            "Аккаунт подключён",
                                            style = MaterialTheme.typography.titleSmall,
                                            color = Hd.Text,
                                        )
                                        Text(
                                            doneHandle ?: "готов к работе",
                                            style = MaterialTheme.typography.bodySmall,
                                            color = Hd.Good,
                                        )
                                    }
                                }
                                Spacer(Modifier.height(8.dp))
                                Text(
                                    "Теперь можно добавлять цели и запускать скан.",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = Hd.Muted,
                                )
                                Spacer(Modifier.height(12.dp))
                                Button(
                                    onClick = { reset() },
                                    shape = RoundedCornerShape(12.dp),
                                    colors = ButtonDefaults.buttonColors(
                                        containerColor = Hd.Accent,
                                        contentColor = Hd.Black,
                                    ),
                                    modifier = Modifier.fillMaxWidth().heightIn(min = 46.dp),
                                ) {
                                    Text("Готово", fontWeight = FontWeight.SemiBold)
                                }
                            }

                            LoginStep.Idle -> Unit
                        }

                        error?.let { message ->
                            Spacer(Modifier.height(10.dp))
                            Text(
                                message,
                                style = MaterialTheme.typography.labelMedium,
                                color = Hd.Bad,
                            )
                        }
                    }
                }
            }

            if (step == LoginStep.Idle && vm.sessions.isNotEmpty()) {
                item("add") {
                    OutlinedButton(
                        onClick = { step = LoginStep.Phone; error = null },
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.fillMaxWidth().heightIn(min = 46.dp),
                    ) {
                        Text(
                            "Добавить ещё аккаунт",
                            color = Hd.Accent2,
                            style = MaterialTheme.typography.labelLarge,
                        )
                    }
                }
            }
        }
    }

    toDelete?.let { session ->
        AlertDialog(
            onDismissRequest = { toDelete = null },
            title = { Text("Удалить аккаунт?") },
            text = {
                Text(
                    "«${session.name}» отключится от приложения, файл сессии удалится. " +
                        "Сам Telegram-аккаунт останется — выйти из него можно в настройках Telegram.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Hd.Muted,
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    toDelete = null
                    vm.deleteSession(session.name) { result ->
                        result.onSuccess { onToast("Аккаунт удалён") }
                            .onFailure { onToast(it.message ?: "Не удалось удалить") }
                    }
                }) {
                    Text("Удалить", color = Hd.Bad)
                }
            },
            dismissButton = {
                TextButton(onClick = { toDelete = null }) {
                    Text("Отмена", color = Hd.Muted)
                }
            },
            containerColor = Hd.Surface2,
            titleContentColor = Hd.Text,
            textContentColor = Hd.Muted,
        )
    }
}

@Composable
private fun SessionRow(session: SessionInfo, onDelete: () -> Unit) {
    HdCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Avatar(
                name = session.username ?: session.name,
                seed = session.tgUserId,
                size = 42,
            )
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    text = session.username?.let { "@$it" } ?: session.name,
                    style = MaterialTheme.typography.titleSmall,
                    color = Hd.Text,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    text = buildString {
                        append(if (session.active) "активен" else session.status)
                        if (session.groupsCount > 0) append(" · чатов: ${session.groupsCount}")
                        append(" · файл: ${session.name}")
                    },
                    style = MaterialTheme.typography.labelSmall,
                    color = Hd.Muted,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            IconButton(onClick = onDelete) {
                Icon(
                    Icons.Filled.Delete,
                    contentDescription = "Удалить аккаунт ${session.name}",
                    tint = Hd.Dim,
                )
            }
        }
    }
}
