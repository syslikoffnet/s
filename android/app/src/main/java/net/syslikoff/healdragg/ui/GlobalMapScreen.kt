package net.syslikoff.healdragg.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.ChatBubble
import androidx.compose.material.icons.filled.GpsFixed
import androidx.compose.material.icons.filled.People
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Общая карта сети: все цели, их чаты и собеседники в одном полотне.
 *
 * Раскладка — пружинная: узлы расталкиваются, связи притягивают. Считается
 * один раз на набор данных, поэтому на телефоне это дешёво; дальше — только
 * жесты (зум, сдвиг, тап по узлу).
 */
@Composable
fun GlobalMapScreen(
    api: Api?,
    onBack: () -> Unit,
    onOpenProfile: (String) -> Unit = {},
    onToast: (String) -> Unit = {},
) {
    var graph by remember { mutableStateOf<GlobalMap?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var selected by remember { mutableStateOf<MapNode?>(null) }
    var scale by remember { mutableStateOf(1f) }
    var pan by remember { mutableStateOf(Offset.Zero) }
    var canvasSize by remember { mutableStateOf(IntSize.Zero) }
    var reloadKey by remember { mutableStateOf(0) }

    LaunchedEffect(api, reloadKey) {
        val client = api
        if (client == null) {
            error = "Движок не запущен"
            loading = false
            return@LaunchedEffect
        }
        loading = true
        runCatching { client.globalGraph() }
            .onSuccess {
                graph = it
                error = null
            }
            .onFailure { error = it.message ?: "Не удалось построить карту" }
        loading = false
    }

    val nodes = remember(graph) { limitedNodes(graph) }
    val positions = remember(nodes) { springLayout(nodes, graph?.links.orEmpty()) }

    Column(Modifier.fillMaxSize().padding(horizontal = 12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = onBack) {
                Icon(Icons.Filled.ArrowBack, contentDescription = "Назад", tint = Hd.Muted)
            }
            Text(
                "Карта сети",
                style = MaterialTheme.typography.titleMedium,
                color = Hd.Text,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            IconButton(onClick = { reloadKey += 1 }) {
                Icon(Icons.Filled.Refresh, contentDescription = "Обновить", tint = Hd.Accent2)
            }
        }

        val current = graph
        if (loading && current == null) {
            LoadingBlock()
            return@Column
        }
        error?.let {
            ErrorBanner(message = it)
            Spacer(Modifier.height(6.dp))
        }
        if (current == null) return@Column

        Row(
            Modifier.fillMaxWidth().padding(bottom = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Chip(current.targets.toString(), Hd.Accent, icon = Icons.Filled.GpsFixed)
            Chip(current.chats.toString(), Hd.Accent2, icon = Icons.Filled.ChatBubble)
            Chip(current.peers.toString(), Hd.Good, icon = Icons.Filled.People)
            Spacer(Modifier.weight(1f))
            TextButton(onClick = {
                scale = 1f
                pan = Offset.Zero
                selected = null
            }) {
                Text("Сброс", color = Hd.Muted, style = MaterialTheme.typography.labelMedium)
            }
        }

        if (nodes.isEmpty()) {
            EmptyState(
                emoji = "",
                icon = Icons.Filled.Share,
                title = "Карта пока пуста",
                text = "Добавь цели и собери их активность — узлы и связи появятся здесь " +
                    "автоматически: цели, чаты и собеседники с весами связей.",
            )
            return@Column
        }

        Box(
            Modifier
                .fillMaxWidth()
                .weight(1f)
                .heightIn(min = 320.dp)
                .clip(RoundedCornerShape(20.dp))
                .background(Hd.Surface)
                .onSizeChanged { canvasSize = it },
        ) {
            val transformState = rememberTransformableState { zoomChange, panChange, _ ->
                scale = (scale * zoomChange).coerceIn(0.4f, 4f)
                pan += panChange
            }
            val hits = remember(nodes) { mutableListOf<Pair<MapNode, Offset>>() }

            Canvas(
                Modifier
                    .fillMaxSize()
                    .transformable(transformState)
                    .pointerInput(nodes, scale, pan, canvasSize) {
                        detectTapGestures { tap ->
                            selected = hits.minByOrNull { (_, pos) -> (pos - tap).getDistance() }
                                ?.takeIf { (_, pos) -> (pos - tap).getDistance() <= 44f }
                                ?.first
                        }
                    },
            ) {
                val center = Offset(size.width / 2f, size.height / 2f)
                val fit = min(size.width, size.height) / 620f
                val k = fit * scale
                fun screen(point: Offset) = center + point * k + pan

                hits.clear()
                nodes.forEach { node ->
                    val point = positions[node.id] ?: return@forEach
                    hits += node to screen(point)
                }

                graph?.links?.forEach { link ->
                    val from = positions[link.source] ?: return@forEach
                    val to = positions[link.target] ?: return@forEach
                    val strength = link.weight.coerceIn(1, 30) / 30f
                    val color = when (link.kind) {
                        "talks" -> Hd.Good.copy(alpha = 0.12f + 0.30f * strength)
                        "in_chat" -> Hd.Warn.copy(alpha = 0.10f + 0.22f * strength)
                        else -> Hd.Accent2.copy(alpha = 0.10f + 0.26f * strength)
                    }
                    drawLine(
                        color = color,
                        start = screen(from),
                        end = screen(to),
                        strokeWidth = (0.7f + 2.0f * strength) * scale.coerceIn(0.7f, 1.6f),
                    )
                }

                nodes.forEach { node ->
                    val point = positions[node.id] ?: return@forEach
                    val weight = node.weight.coerceIn(1, 40)
                    val base = when (node.type) {
                        "target" -> 7f
                        "chat" -> 5f
                        else -> 4f
                    }
                    val radius = (base + weight * 0.30f) * scale.coerceIn(0.6f, 1.8f)
                    val color = when (node.type) {
                        "target" -> Hd.Accent
                        "chat" -> Hd.Accent2
                        else -> Hd.Good
                    }
                    val isSelected = selected?.id == node.id
                    if (isSelected) {
                        drawCircle(
                            color = color.copy(alpha = 0.25f),
                            radius = radius * 2.1f,
                            center = screen(point),
                        )
                    }
                    drawCircle(
                        color = color.copy(alpha = if (isSelected) 1f else 0.9f),
                        radius = radius,
                        center = screen(point),
                    )
                    drawCircle(
                        color = Hd.Surface,
                        radius = radius * 0.42f,
                        center = screen(point),
                    )
                }
            }
        }

        selected?.let { node ->
            Spacer(Modifier.height(8.dp))
            HdCard(contentPadding = androidx.compose.foundation.layout.PaddingValues(14.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        Modifier
                            .size(34.dp)
                            .clip(RoundedCornerShape(10.dp))
                            .background(
                                when (node.type) {
                                    "target" -> Hd.Accent.copy(alpha = 0.18f)
                                    "chat" -> Hd.Accent2.copy(alpha = 0.18f)
                                    else -> Hd.Good.copy(alpha = 0.18f)
                                }
                            ),
                        contentAlignment = Alignment.Center,
                    ) {
                        Icon(
                            when (node.type) {
                                "target" -> Icons.Filled.GpsFixed
                                "chat" -> Icons.Filled.ChatBubble
                                else -> Icons.Filled.People
                            },
                            contentDescription = null,
                            tint = when (node.type) {
                                "target" -> Hd.Accent
                                "chat" -> Hd.Accent2
                                else -> Hd.Good
                            },
                            modifier = Modifier.size(18.dp),
                        )
                    }
                    Spacer(Modifier.width(10.dp))
                    Column(Modifier.weight(1f)) {
                        Text(
                            node.label,
                            style = MaterialTheme.typography.titleSmall,
                            color = Hd.Text,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        Text(
                            buildString {
                                append(node.kindLabel)
                                append(" · вес ")
                                append(node.weight)
                                node.username?.let { append(" · @").append(it) }
                                if (node.tgId != 0L) append(" · id ").append(node.tgId)
                            },
                            style = MaterialTheme.typography.labelSmall,
                            color = Hd.Dim,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    val handle = node.username
                    if (!handle.isNullOrBlank()) {
                        TextButton(onClick = { onOpenProfile(handle) }) {
                            Text("Профиль", color = Hd.Accent2, style = MaterialTheme.typography.labelMedium)
                        }
                    } else if (node.tgId != 0L) {
                        TextButton(onClick = { onOpenProfile(node.tgId.toString()) }) {
                            Text("Профиль", color = Hd.Accent2, style = MaterialTheme.typography.labelMedium)
                        }
                    }
                }
            }
        }
        Spacer(Modifier.height(10.dp))
    }
}

/** Ограничиваем карту, чтобы на телефоне она оставалась читаемой. */
private fun limitedNodes(graph: GlobalMap?): List<MapNode> {
    if (graph == null) return emptyList()
    val targets = graph.nodes.filter { it.type == "target" }.sortedByDescending { it.weight }.take(40)
    val chats = graph.nodes.filter { it.type == "chat" }.sortedByDescending { it.weight }.take(60)
    val peers = graph.nodes.filter { it.type == "peer" }.sortedByDescending { it.weight }.take(80)
    return targets + chats + peers
}

/**
 * Пружинная раскладка: расталкивание всех пар + притяжение по связям.
 * Полностью детерминирована (без случайности), поэтому карта не «прыгает».
 */
private fun springLayout(nodes: List<MapNode>, links: List<MapLink>): Map<String, Offset> {
    if (nodes.isEmpty()) return emptyMap()
    val index = nodes.withIndex().associate { (i, node) -> node.id to i }
    val xs = DoubleArray(nodes.size)
    val ys = DoubleArray(nodes.size)
    val count = nodes.size.toDouble()
    nodes.forEachIndexed { i, node ->
        val angle = 2.0 * Math.PI * i / count
        val radius = if (node.type == "target") 90.0 else 240.0
        xs[i] = cos(angle) * radius
        ys[i] = sin(angle) * radius
    }
    val edges = links.mapNotNull { link ->
        val a = index[link.source] ?: return@mapNotNull null
        val b = index[link.target] ?: return@mapNotNull null
        if (a == b) null else Triple(a, b, link.weight.coerceIn(1, 30).toDouble())
    }

    val dx = DoubleArray(nodes.size)
    val dy = DoubleArray(nodes.size)
    repeat(70) {
        java.util.Arrays.fill(dx, 0.0)
        java.util.Arrays.fill(dy, 0.0)
        for (i in xs.indices) {
            for (j in i + 1 until xs.size) {
                var vx = xs[i] - xs[j]
                var vy = ys[i] - ys[j]
                var dist = sqrt(vx * vx + vy * vy)
                if (dist < 0.01) {
                    vx = 0.1 * (i + 1)
                    vy = 0.1 * (j + 1)
                    dist = sqrt(vx * vx + vy * vy)
                }
                val force = 9000.0 / (dist * dist)
                val fx = vx / dist * force
                val fy = vy / dist * force
                dx[i] += fx
                dy[i] += fy
                dx[j] -= fx
                dy[j] -= fy
            }
        }
        for ((a, b, weight) in edges) {
            var vx = xs[b] - xs[a]
            var vy = ys[b] - ys[a]
            var dist = sqrt(vx * vx + vy * vy)
            if (dist < 0.01) {
                vx = 0.1
                vy = 0.1
                dist = sqrt(vx * vx + vy * vy)
            }
            val force = (dist - 60.0) * 0.02 * weight
            val fx = vx / dist * force
            val fy = vy / dist * force
            dx[a] += fx
            dy[a] += fy
            dx[b] -= fx
            dy[b] -= fy
        }
        for (i in xs.indices) {
            // мягкое притяжение к центру, чтобы карта не разлеталась
            dx[i] -= xs[i] * 0.002
            dy[i] -= ys[i] * 0.002
            val delta = sqrt(dx[i] * dx[i] + dy[i] * dy[i])
            val limit = 30.0
            if (delta > limit) {
                dx[i] = dx[i] / delta * limit
                dy[i] = dy[i] / delta * limit
            }
            xs[i] += dx[i]
            ys[i] += dy[i]
        }
    }
    return nodes.mapIndexed { i, node -> node.id to Offset(xs[i].toFloat(), ys[i].toFloat()) }.toMap()
}
