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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Campaign
import androidx.compose.material.icons.filled.CardGiftcard
import androidx.compose.material.icons.filled.ChatBubble
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Done
import androidx.compose.material.icons.filled.Login
import androidx.compose.material.icons.filled.Logout
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Composable
fun AlertsScreen(
    vm: AppViewModel,
    onOpenTarget: (Long) -> Unit,
    onToast: (String) -> Unit,
) {
    var unreadOnly by rememberSaveable { mutableStateOf(false) }

    val rows = remember(vm.alerts, unreadOnly) {
        if (unreadOnly) vm.alerts.filterNot { it.read } else vm.alerts
    }

    LaunchedEffect(Unit) { vm.refreshAlerts() }

    RefreshableBox(
        refreshing = vm.refreshing,
        onRefresh = {
            vm.refreshAlerts()
            vm.refresh(force = true)
        },
        modifier = Modifier.fillMaxSize(),
    ) {
        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 6.dp, bottom = 24.dp),
        ) {
            item("header") {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = if (vm.unread > 0) "Непрочитанных: ${vm.unread}" else "Всё прочитано",
                        style = MaterialTheme.typography.bodySmall,
                        color = if (vm.unread > 0) Hd.Warn else Hd.Muted,
                        modifier = Modifier.weight(1f),
                    )
                    if (vm.unread > 0) {
                        TextButton(onClick = { vm.markAllAlertsRead() }) {
                            Icon(
                                Icons.Filled.Done,
                                contentDescription = null,
                                tint = Hd.Good,
                                modifier = Modifier.size(16.dp),
                            )
                            Spacer(Modifier.width(4.dp))
                            Text("Прочитать все", color = Hd.Good, style = MaterialTheme.typography.labelLarge)
                        }
                    }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(
                        selected = !unreadOnly,
                        onClick = { unreadOnly = false },
                        label = { Text("Все · ${vm.alerts.size}") },
                        colors = filterColors(),
                    )
                    FilterChip(
                        selected = unreadOnly,
                        onClick = { unreadOnly = true },
                        label = { Text("Непрочитанные · ${vm.alerts.count { !it.read }}") },
                        colors = filterColors(),
                    )
                }
                Spacer(Modifier.height(10.dp))
            }

            vm.lastError?.let { message ->
                item("error") {
                    ErrorBanner(message = message, onRetry = { vm.refresh(force = true) })
                }
            }

            if (rows.isEmpty()) {
                item("empty") {
                    EmptyState(
                        emoji = "",
                        icon = if (unreadOnly) Icons.Filled.CheckCircle else Icons.Filled.Notifications,
                        title = if (unreadOnly) "Непрочитанных нет" else "Алертов пока нет",
                        text = "Когда цель зайдёт в новый чат, выйдет из него или напишет — событие появится здесь. " +
                            "Такие же уведомления приходят в шторку Android.",
                    )
                }
            }

            var lastDay: String? = null
            rows.forEach { alert ->
                val day = dayLabel(alert.ts)
                if (day != lastDay) {
                    lastDay = day
                    item("day-${alert.id}") { SectionLabel(day, Modifier.padding(top = 8.dp)) }
                }
                item("alert-${alert.id}", contentType = "alert") {
                    AlertCard(
                        alert = alert,
                        onClick = {
                            if (!alert.read) vm.markAlertRead(alert.id)
                            if (alert.targetTgId > 0) onOpenTarget(alert.targetTgId)
                            else onToast("Алерт без цели")
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun filterColors() = FilterChipDefaults.filterChipColors(
    selectedContainerColor = Hd.Accent.copy(alpha = 0.18f),
    selectedLabelColor = Hd.Accent,
    labelColor = Hd.Muted,
    containerColor = Hd.Surface,
)

@Composable
private fun AlertCard(alert: AlertItem, onClick: () -> Unit) {
    val (icon, tint) = when (alert.kind.lowercase()) {
        "join", "joined" -> Icons.Filled.Login to Hd.Good
        "leave", "left" -> Icons.Filled.Logout to Hd.Bad
        "message", "msg", "post" -> Icons.Filled.ChatBubble to Hd.Accent2
        "mention" -> Icons.Filled.Campaign to Hd.Warn
        "gift" -> Icons.Filled.CardGiftcard to Hd.Accent
        else -> Icons.Filled.Notifications to Hd.Accent
    }
    HdCard(onClick = onClick, contentPadding = PaddingValues(14.dp)) {
        Row(verticalAlignment = Alignment.Top) {
            Box(
                Modifier
                    .size(38.dp)
                    .background(tint.copy(alpha = 0.14f), RoundedCornerShape(12.dp)),
                contentAlignment = Alignment.Center,
            ) {
                Icon(icon, contentDescription = null, tint = tint, modifier = Modifier.size(18.dp))
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        alert.title,
                        style = MaterialTheme.typography.titleSmall,
                        color = Hd.Text,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                    if (!alert.read) {
                        Spacer(Modifier.width(6.dp))
                        Box(
                            Modifier
                                .size(8.dp)
                                .background(Hd.Bad, CircleShape),
                        )
                    }
                }
                alert.body?.let { body ->
                    Spacer(Modifier.height(2.dp))
                    Text(
                        body,
                        style = MaterialTheme.typography.bodySmall,
                        color = Hd.Muted,
                        maxLines = 4,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                Spacer(Modifier.height(6.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    alert.targetUsername?.let { username ->
                        Chip("@$username", Hd.Accent2)
                        Spacer(Modifier.width(6.dp))
                    } ?: alert.targetTgId.takeIf { it > 0 }?.let { id ->
                        Chip("ID $id", Hd.Dim)
                        Spacer(Modifier.width(6.dp))
                    }
                    Text(
                        timeOnly(alert.ts),
                        style = MaterialTheme.typography.labelSmall,
                        color = Hd.Dim,
                    )
                    Spacer(Modifier.width(6.dp))
                    Text(
                        "· ${relativeTime(alert.ts)}",
                        style = MaterialTheme.typography.labelSmall,
                        color = Hd.Dim.copy(alpha = if (alert.read) 0.6f else 1f),
                        fontFamily = FontFamily.Monospace,
                        maxLines = 1,
                    )
                }
            }
        }
    }
}
