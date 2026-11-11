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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ChatBubble
import androidx.compose.material.icons.filled.GpsFixed
import androidx.compose.material.icons.filled.People
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.FilterChip
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
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlin.math.min

/**
 * Паутина наблюдаемых связей: пружины, отталкивание и направленные подарки.
 * Масштаб, сдвиг и перемещение выбранного узла — жестами.
 */
@Composable
fun GraphTab(api: Api?, tgId: Long, onToast: (String) -> Unit, onOpenProfile: (String) -> Unit) {
    val context = LocalContext.current
    var data by remember(tgId) { mutableStateOf<GraphData?>(null) }
    var loading by remember(tgId) { mutableStateOf(true) }
    var error by remember(tgId) { mutableStateOf<String?>(null) }
    var selected by remember(tgId) { mutableStateOf<GraphNode?>(null) }

    var scale by remember(tgId) { mutableStateOf(1f) }
    var pan by remember(tgId) { mutableStateOf(Offset.Zero) }
    var canvasSize by remember { mutableStateOf(IntSize.Zero) }
    var layer by remember(tgId) { mutableStateOf("all") }
    var moveNode by remember(tgId) { mutableStateOf(false) }
    var layoutVersion by remember(tgId) { mutableStateOf(0) }
    var animationKick by remember(tgId) { mutableStateOf(0) }
    var nodeSearch by remember(tgId) { mutableStateOf("") }
    val positions = remember(tgId) { mutableListOf<Pair<GraphNode, Offset>>() }

    LaunchedEffect(tgId, api) {
        loading = true
        val client = api
        if (client == null) {
            error = "Движок не запущен"
            loading = false
            return@LaunchedEffect
        }
        while (true) {
            runCatching { client.graph(tgId) }
                .onSuccess {
                    data = it
                    selected = selected?.let { old -> it.nodes.firstOrNull { n -> n.id == old.id } }
                    error = null
                }
                .onFailure { error = it.message }
            loading = false
            delay(10_000)
        }
    }

    Column(Modifier.fillMaxWidth()) {
        val graph = data
        if (loading && graph == null) {
            LoadingBlock()
            return@Column
        }
        error?.let {
            ErrorBanner(message = it)
            Spacer(Modifier.height(8.dp))
        }
        if (graph == null) return@Column

        val allowedKinds = when (layer) {
            "talks" -> setOf("peer", "context", "chat")
            "gifts" -> setOf("gift_in", "gift_out")
            "chats" -> setOf("chat")
            else -> setOf("peer", "context", "chat", "gift_in", "gift_out")
        }
        val candidateEdges = graph.edges.filter { it.kind in allowedKinds }
        val connectedIds = candidateEdges.flatMap { listOf(it.source, it.target) }.toSet()
        val shownNodes = buildList {
            graph.nodes.firstOrNull { it.type == "target" }?.let { add(it) }
            addAll(graph.nodes.filter { it.type == "chat" && it.id in connectedIds }.sortedByDescending { it.weight }.take(40))
            addAll(graph.nodes.filter { it.type == "peer" && it.id in connectedIds }.sortedByDescending { it.weight }.take(72))
        }
        val shownIds = shownNodes.map { it.id }.toSet()
        val shownEdges = candidateEdges.filter { it.source in shownIds && it.target in shownIds }
        val layoutKey = shownNodes.map { it.id }.sorted() to shownEdges.map { "${it.source}:${it.target}:${it.kind}:${it.weight}" }.sorted()
        val engine = remember(layoutKey, layoutVersion) { GraphPhysics(shownNodes.map { it.id }, shownEdges) }
        var world by remember(engine) { mutableStateOf(engine.positions()) }
        LaunchedEffect(engine, layoutVersion, animationKick) {
            while (engine.step()) {
                withFrameNanos { }
                world = engine.positions()
            }
        }
        val selectedId = selected?.id?.takeIf { it in shownIds }
        val related = shownEdges.filter { it.source == selectedId || it.target == selectedId }
            .flatMap { listOf(it.source, it.target) }.toSet()

        val peers = graph.nodes.count { it.type == "peer" }
        Row(
            Modifier.fillMaxWidth().padding(bottom = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Chip("профиль", Hd.Accent, icon = Icons.Filled.GpsFixed)
            Chip("$peers собеседников", Hd.Good, icon = Icons.Filled.People)
            Spacer(Modifier.weight(1f))
            TextButton(onClick = {
                scale = 1f
                pan = Offset.Zero
                selected = null
                moveNode = false
                layoutVersion++
            }) { Text("Сброс", color = Hd.Muted, style = MaterialTheme.typography.labelMedium) }
        }

        if (graph.nodes.none { it.type == "peer" || it.type == "chat" }) {
            EmptyState(
                emoji = "",
                icon = Icons.Filled.Share,
                title = "Паутина пуста",
                text = "Соберите чаты, активность или подарки профиля. Данные зависят от доступной истории и настроек приватности Telegram.",
            )
            return@Column
        }

        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            listOf("all" to "Все связи", "talks" to "Общение", "gifts" to "Подарки", "chats" to "Чаты").forEach { (value, label) ->
                FilterChip(selected = layer == value, onClick = { layer = value; selected = null; moveNode = false }, label = { Text(label) })
            }
        }
        Text("Показано ${shownNodes.size} из ${graph.nodes.size} узлов · ${shownEdges.size} связей. Голубой — чат, зелёный — ответ/упоминание, жёлтая и розовая стрелки — записи подарков. Контекстная линия — реальное упоминание или ответ в чате, не просто общее участие. Данные неполны и не доказывают текущее владение NFT.", color = Hd.Muted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(bottom = 8.dp))
        HdTextField(nodeSearch, { nodeSearch = it }, "Найти узел", hint = "Имя, @username или ID")
        if (nodeSearch.isNotBlank()) Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            shownNodes.filter { it.label.contains(nodeSearch.trim(), true) || (it.username ?: "").contains(nodeSearch.trim().removePrefix("@"), true) || it.tgId.toString().contains(nodeSearch.trim()) }.take(12).forEach { node ->
                FilterChip(selected = selectedId == node.id, onClick = { selected = node; moveNode = false }, label = { Text(node.label.take(18)) })
            }
        }
        if (selectedId != null && selectedId != "target") {
            TextButton(onClick = { moveNode = !moveNode }) { Text(if (moveNode) "Перемещение узла: включено" else "Переместить выбранный узел") }
        }
        Box(
            Modifier
                .fillMaxWidth()
                .height(430.dp)
                .clip(RoundedCornerShape(20.dp))
                .background(Hd.Surface)
                .onSizeChanged { canvasSize = it },
        ) {
            val transformState = rememberTransformableState { zoomChange, panChange, _ ->
                scale = (scale * zoomChange).coerceIn(0.45f, 3.2f)
                if (moveNode && selectedId != null && selectedId != "target" && zoomChange in 0.98f..1.02f) {
                    val fit = min(canvasSize.width, canvasSize.height) / 620f * scale
                    if (fit > 0f) {
                        engine.move(selectedId, (panChange.x / fit).toDouble(), (panChange.y / fit).toDouble())
                        world = engine.positions()
                        animationKick++
                    }
                } else pan += panChange
            }
            val density = androidx.compose.ui.platform.LocalDensity.current
            val labelPaint = remember {
                Paint(Paint.ANTI_ALIAS_FLAG).apply {
                    typeface = Typeface.create(Typeface.MONOSPACE, Typeface.NORMAL)
                }
            }
            val labelSize = with(density) { 10.5.sp.toPx() }
            val tapRadius = with(density) { 30.dp.toPx() }

            Canvas(
                Modifier
                    .fillMaxSize()
                    .transformable(transformState)
                    .pointerInput(data, scale, pan) {
                        detectTapGestures { tap ->
                            val hit = positions.minByOrNull { (_, pos) ->
                                (pos - tap).getDistance()
                            }?.takeIf { (_, pos) -> (pos - tap).getDistance() <= tapRadius }
                            selected = hit?.first
                            moveNode = false
                        }
                    },
            ) {
                val center = Offset(size.width / 2f, size.height / 2f)
                val fit = min(size.width, size.height) / 620f
                fun screen(point: Pair<Float, Float>): Offset = center + Offset(point.first, point.second) * (fit * scale) + pan
                val targetNode = shownNodes.firstOrNull { it.type == "target" }
                val peerNodes = shownNodes.filter { it.type == "peer" }
                val chatNodes = shownNodes.filter { it.type == "chat" }

                positions.clear()
                shownNodes.forEach { node ->
                    world[node.id]?.let { positions += node to screen(it) }
                }

                // рёбра
                shownEdges.forEach { edge ->
                    val from = world[edge.source] ?: return@forEach
                    val to = world[edge.target] ?: return@forEach
                    val strength = edge.weight.coerceIn(1, 40) / 40f
                    val giftEdge = edge.kind == "gift_in" || edge.kind == "gift_out"
                    val edgeColor = when (edge.kind) {
                        "gift_in" -> Hd.Warn.copy(alpha = 0.8f)
                        "gift_out" -> Hd.Accent.copy(alpha = 0.8f)
                        "peer" -> Hd.Good.copy(alpha = 0.28f + 0.42f * strength)
                        "context" -> Hd.Good.copy(alpha = 0.15f + 0.35f * strength)
                        else -> Hd.Accent2.copy(alpha = 0.14f + 0.28f * strength)
                    }
                    val a = screen(from)
                    val b = screen(to)
                    val highlighted = selectedId == null || edge.source == selectedId || edge.target == selectedId
                    drawLine(color = edgeColor.copy(alpha = edgeColor.alpha * if (highlighted) 1f else 0.14f), start = a, end = b,
                        strokeWidth = (0.6f + 2.2f * strength) * scale.coerceIn(0.7f, 1.6f))
                    if (giftEdge) {
                        val delta = b - a
                        val length = delta.getDistance()
                        if (length > 0f) {
                            val unit = delta / length
                            val side = Offset(-unit.y, unit.x)
                            val tip = a + delta * 0.72f
                            val back = tip - unit * 10f
                            drawLine(edgeColor, tip, back + side * 5f, strokeWidth = 2f)
                            drawLine(edgeColor, tip, back - side * 5f, strokeWidth = 2f)
                        }
                    }
                }

                // узлы
                shownNodes.forEach { node ->
                    val pos = world[node.id]?.let { screen(it) } ?: return@forEach
                    val weight = node.weight.coerceIn(1, 40)
                    val radius = (5f + weight * 0.35f) * scale.coerceIn(0.6f, 1.8f)
                    val color = when (node.type) {
                        "target" -> Hd.Accent
                        "peer" -> Hd.Good
                        else -> Hd.Accent2
                    }
                    if (selected?.id == node.id) {
                        drawCircle(color.copy(alpha = 0.22f), radius + 10f, pos)
                    }
                    drawCircle(color.copy(alpha = 0.22f), radius + 5f, pos)
                    drawCircle(color.copy(alpha = if (selectedId == null || node.id == selectedId || node.id in related) 1f else 0.20f), radius, pos)
                    if (node.type == "target") {
                        drawCircle(Hd.Bg, radius * 0.42f, pos)
                    }
                }

                // подписи: цель и самые весомые узлы
                labelPaint.textSize = labelSize
                labelPaint.color = Hd.Text.copy(alpha = 0.92f).toArgb()
                val labelled = buildList {
                    targetNode?.let { add(it) }
                    addAll(peerNodes.take(12))
                    addAll(chatNodes.take(5))
                    selected?.takeIf { it.id in shownIds }?.let { if (it !in this) add(it) }
                }
                labelled.forEach { node ->
                    val pos = world[node.id]?.let { screen(it) } ?: return@forEach
                    val text = node.label.take(22)
                    val width = labelPaint.measureText(text)
                    drawIntoCanvas { canvas ->
                        canvas.nativeCanvas.drawText(
                            text,
                            pos.x - width / 2f,
                            pos.y + 20f + 12f * scale.coerceIn(0.6f, 1.4f),
                            labelPaint,
                        )
                    }
                }
            }

            Box(
                Modifier
                    .align(Alignment.BottomStart)
                    .padding(10.dp)
                    .background(Hd.Bg.copy(alpha = 0.7f), RoundedCornerShape(10.dp))
                    .padding(horizontal = 8.dp, vertical = 4.dp),
            ) {
                Text(
                    if (moveNode) "тяни — двигать выбранный узел · щипок — зум" else "щипок — зум · тяни — сдвиг · тап — узел",
                    style = MaterialTheme.typography.labelSmall,
                    color = Hd.Dim,
                )
            }
        }

        selected?.let { node ->
            Spacer(Modifier.height(10.dp))
            if (node.id in shownIds) {
                val incident = shownEdges.filter { it.source == node.id || it.target == node.id }
                val evidence = incident.groupBy { it.kind }.map { (kind, links) ->
                    val label = when (kind) { "context" -> "контекст в чатах"; "peer" -> "ответы/упоминания"; "gift_in" -> "записи от дарителя"; "gift_out" -> "записи в профиле"; else -> "связи с чатами" }
                    "$label: ${links.sumOf { it.weight }}"
                }
                Text(evidence.joinToString(" · "), color = Hd.Muted, style = MaterialTheme.typography.labelSmall)
            }
            NodeCard(
                node = node,
                onClose = { selected = null; moveNode = false },
                onToast = onToast,
                onOpenProfile = { onOpenProfile(node.tgId.toString()) },
                onOpen = {
                    val link = node.link ?: telegramLink(node.username)
                    if (!openLink(context, link)) onToast("Нет ссылки Telegram")
                },
            )
        }
    }
}

@Composable
private fun NodeCard(
    node: GraphNode,
    onClose: () -> Unit,
    onOpen: () -> Unit,
    onOpenProfile: () -> Unit,
    onToast: (String) -> Unit,
) {
    val context = LocalContext.current
    val meta = node.meta
    HdCard(contentPadding = PaddingValues(14.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier
                    .size(10.dp)
                    .background(
                        when (node.type) {
                            "target" -> Hd.Accent
                            "peer" -> Hd.Good
                            else -> Hd.Accent2
                        },
                        CircleShape,
                    ),
            )
            Spacer(Modifier.width(8.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    node.label,
                    style = MaterialTheme.typography.titleSmall,
                    color = Hd.Text,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    when (node.type) {
                        "target" -> "профиль"
                        "peer" -> "собеседник"
                        else -> "чат"
                    } + " · ID ${node.tgId}",
                    style = MaterialTheme.typography.labelSmall,
                    color = Hd.Dim,
                    fontFamily = FontFamily.Monospace,
                )
            }
            TextButton(onClick = onClose) { Text("✕", color = Hd.Muted) }
        }
        meta?.let { m ->
            Spacer(Modifier.height(6.dp))
            val lines = buildList {
                m.str("username")?.let { add("@$it") }
                m.str("status")?.let { add(Hd.statusLabel(it)) }
                m.int("members").takeIf { it > 0 }?.let { add("${compactNumber(it)} участн.") }
                m.int("messages").takeIf { it > 0 }?.let { add("${compactNumber(it)} сообщ.") }
                m.int("my_messages").takeIf { it > 0 }?.let { add("${it} от профиля") }
                m.int("replies").takeIf { it > 0 }?.let { add("${it} ответов") }
                m.int("mentions").takeIf { it > 0 }?.let { add("${it} упоминаний") }
                m.int("gifts_received").takeIf { it > 0 }?.let { add("${it} записей от этого пользователя") }
                m.int("gifts_sent").takeIf { it > 0 }?.let { add("${it} записей в его профиле") }
                m.num("last").takeIf { it > 0 }?.let { add("последнее наблюдение: ${fullDate(it)}") }
            }
            if (lines.isNotEmpty()) {
                Text(
                    lines.joinToString("  ·  "),
                    style = MaterialTheme.typography.bodySmall,
                    color = Hd.Muted,
                )
            }
        }
        if (node.type == "peer") {
            val chats = meta?.optJSONArray("chats")
            if (chats != null && chats.length() > 0) {
                Spacer(Modifier.height(8.dp))
                Text("Где общались", style = MaterialTheme.typography.labelMedium, color = Hd.Text)
                for (i in 0 until min(chats.length(), 8)) {
                    val chat = chats.optJSONObject(i) ?: continue
                    Text(
                        "${chat.optString("title", "Чат")} · ${chat.optInt("interactions")} связей",
                        style = MaterialTheme.typography.bodySmall,
                        color = Hd.Muted,
                    )
                }
            }
        }
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (node.type == "peer") {
                TextButton(onClick = onOpenProfile) {
                    Text("Профиль", color = Hd.Accent, style = MaterialTheme.typography.labelLarge)
                }
            }
            TextButton(onClick = onOpen) {
                Text("Открыть в Telegram", color = Hd.Accent2, style = MaterialTheme.typography.labelLarge)
            }
            TextButton(onClick = {
                copyText(context, "tg", node.tgId.toString())
                onToast("ID скопирован")
            }) {
                Text("Скопировать ID", color = Hd.Muted, style = MaterialTheme.typography.labelLarge)
            }
        }
    }
}
