package net.syslikoff.healdragg.ui

import android.graphics.Paint
import android.graphics.Typeface
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
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
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.withFrameNanos
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.min

/**
 * Общая карта сети: все цели, их чаты и собеседники в одном полотне.
 *
 * Пружинная анимированная раскладка, фильтры и контекст наблюдений.
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
    var layer by remember { mutableStateOf("all") }
    var moveNode by remember { mutableStateOf(false) }
    var layoutVersion by remember { mutableStateOf(0) }
    var animationKick by remember { mutableStateOf(0) }
    var nodeSearch by remember { mutableStateOf("") }

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

    val visibleLinks = graph?.links.orEmpty().filter {
        when (layer) { "talks" -> it.kind == "talks" || it.kind == "in_chat"; "gifts" -> it.kind == "gift"; "chats" -> it.kind == "writes"; else -> true }
    }
    val linkedIds = visibleLinks.flatMap { listOf(it.source, it.target) }.toSet()
    val nodes = limitedNodes(graph).filter { layer == "all" || it.id in linkedIds }
    val nodeIds = nodes.map { it.id }.toSet()
    val edges = visibleLinks.filter { it.source in nodeIds && it.target in nodeIds }
    val layoutKey = nodes.map { it.id }.sorted() to edges.map { "${it.source}:${it.target}:${it.kind}:${it.weight}" }.sorted()
    val anchor = nodes.firstOrNull { it.type == "target" }?.id ?: nodes.firstOrNull()?.id ?: ""
    val engine = remember(layoutKey, layoutVersion) { GraphPhysics(nodes.map { it.id }, edges.map { GraphEdge(it.source, it.target, it.weight, it.kind) }, anchor) }
    var positions by remember(engine) { mutableStateOf(engine.positions()) }
    LaunchedEffect(engine, animationKick) {
        while (engine.step()) {
            withFrameNanos { }
            positions = engine.positions()
        }
    }
    val selectedId = selected?.id?.takeIf { it in nodeIds }
    val connected = edges.filter { it.source == selectedId || it.target == selectedId }.flatMap { listOf(it.source, it.target) }.toSet()

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
                moveNode = false
                layoutVersion++
            }) {
                Text("Сброс", color = Hd.Muted, style = MaterialTheme.typography.labelMedium)
            }
        }

        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            listOf("all" to "Все", "talks" to "Общение", "gifts" to "Подарки", "chats" to "Чаты").forEach { (value, label) ->
                FilterChip(selected = layer == value, onClick = { layer = value; selected = null; moveNode = false }, label = { Text(label) })
            }
        }
        Text("${nodes.size} узлов · ${edges.size} связей на карте. Подарки — записи профилей, не доказательство сделки или текущего владения; общие чаты не означают общение.", color = Hd.Muted, style = MaterialTheme.typography.labelSmall)
        HdTextField(nodeSearch, { nodeSearch = it }, "Найти на карте", hint = "Имя, @username или ID")
        if (nodeSearch.isNotBlank()) {
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                nodes.filter { it.label.contains(nodeSearch.trim(), true) || (it.username ?: "").contains(nodeSearch.trim().removePrefix("@"), true) || it.tgId.toString().contains(nodeSearch.trim()) }.take(12).forEach { node ->
                    FilterChip(selected = selectedId == node.id, onClick = { selected = node; moveNode = false }, label = { Text(node.label.take(18)) })
                }
            }
        }
        if (selectedId != null && selectedId != anchor) TextButton(onClick = { moveNode = !moveNode }) {
            Text(if (moveNode) "Перемещение узла: включено" else "Переместить выбранный узел")
        }

        if (nodes.isEmpty()) {
            EmptyState(
                emoji = "",
                icon = Icons.Filled.Share,
                title = "Карта пока пуста",
                text = "Открой профили и собери их активность — узлы и связи появятся здесь " +
                    "автоматически: профили, чаты и собеседники с весами связей.",
            )
            return@Column
        }

        Box(
            Modifier
                .fillMaxWidth()
                .weight(1f)
                .heightIn(min = 180.dp)
                .clip(RoundedCornerShape(20.dp))
                .background(Hd.Surface)
                .onSizeChanged { canvasSize = it },
        ) {
            val transformState = rememberTransformableState { zoomChange, panChange, _ ->
                scale = (scale * zoomChange).coerceIn(0.4f, 4f)
                if (moveNode && selectedId != null && selectedId != anchor && zoomChange in 0.98f..1.02f) {
                    val fit = min(canvasSize.width, canvasSize.height) / 620f * scale
                    if (fit > 0f) {
                        engine.move(selectedId, (panChange.x / fit).toDouble(), (panChange.y / fit).toDouble())
                        positions = engine.positions()
                        animationKick++
                    }
                } else pan += panChange
            }
            val hits = remember(nodes) { mutableListOf<Pair<MapNode, Offset>>() }
            val density = androidx.compose.ui.platform.LocalDensity.current
            val labelPaint = remember { Paint(Paint.ANTI_ALIAS_FLAG).apply { typeface = Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD) } }
            val labelSize = with(density) { 10.sp.toPx() }
            val tapRadius = with(density) { 28.dp.toPx() }

            Canvas(
                Modifier
                    .fillMaxSize()
                    .transformable(transformState)
                    .pointerInput(nodes, scale, pan, canvasSize) {
                        detectTapGestures { tap ->
                            selected = hits.minByOrNull { (_, pos) -> (pos - tap).getDistance() }
                                ?.takeIf { (_, pos) -> (pos - tap).getDistance() <= tapRadius }
                                ?.first
                            moveNode = false
                        }
                    },
            ) {
                val center = Offset(size.width / 2f, size.height / 2f)
                val fit = min(size.width, size.height) / 620f
                val k = fit * scale
                fun screen(point: Pair<Float, Float>) = center + Offset(point.first, point.second) * k + pan

                drawRect(brush = Brush.radialGradient(listOf(Hd.Accent2.copy(alpha = 0.10f), Color.Transparent), center = center, radius = size.maxDimension * 0.75f))
                hits.clear()
                nodes.forEach { node ->
                    val point = positions[node.id] ?: return@forEach
                    hits += node to screen(point)
                }

                edges.forEach { link ->
                    val from = positions[link.source] ?: return@forEach
                    val to = positions[link.target] ?: return@forEach
                    val strength = link.weight.coerceIn(1, 30) / 30f
                    val color = when (link.kind) {
                        "gift" -> Hd.Warn.copy(alpha = 0.75f)
                        "talks" -> Hd.Good.copy(alpha = 0.12f + 0.30f * strength)
                        "in_chat" -> Hd.Warn.copy(alpha = 0.10f + 0.22f * strength)
                        else -> Hd.Accent2.copy(alpha = 0.10f + 0.26f * strength)
                    }
                    drawLine(
                        color = color.copy(alpha = color.alpha * if (selectedId == null || link.source == selectedId || link.target == selectedId) 1f else 0.15f),
                        start = screen(from),
                        end = screen(to),
                        strokeWidth = (0.7f + 2.0f * strength) * scale.coerceIn(0.7f, 1.6f),
                    )
                    if (link.kind == "gift") {
                        val a = screen(from); val b = screen(to); val delta = b - a; val length = delta.getDistance()
                        if (length > 0f) {
                            val unit = delta / length; val side = Offset(-unit.y, unit.x); val tip = a + delta * 0.72f
                            drawLine(color, tip, tip - unit * 9f + side * 4f, strokeWidth = 2f)
                            drawLine(color, tip, tip - unit * 9f - side * 4f, strokeWidth = 2f)
                        }
                    }
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
                        color = color.copy(alpha = if (selectedId == null || isSelected || node.id in connected) 0.95f else 0.20f),
                        radius = radius,
                        center = screen(point),
                    )
                    drawCircle(
                        color = Hd.Surface,
                        radius = radius * 0.42f,
                        center = screen(point),
                    )
                }
                labelPaint.textSize = labelSize
                labelPaint.color = Hd.Text.copy(alpha = 0.9f).toArgb()
                val labelled = (nodes.filter { it.type == "target" }.take(8) + nodes.sortedByDescending { it.weight }.take(7) + listOfNotNull(selected)).distinctBy { it.id }
                labelled.forEach { node ->
                    val p = positions[node.id]?.let { screen(it) } ?: return@forEach
                    val label = node.label.take(20)
                    drawIntoCanvas { canvas -> canvas.nativeCanvas.drawText(label, p.x - labelPaint.measureText(label) / 2f, p.y + 23f, labelPaint) }
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
                    if (node.type != "chat" && !handle.isNullOrBlank()) {
                        TextButton(onClick = { onOpenProfile(handle) }) {
                            Text("Профиль", color = Hd.Accent2, style = MaterialTheme.typography.labelMedium)
                        }
                    } else if (node.type != "chat" && node.tgId != 0L) {
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
