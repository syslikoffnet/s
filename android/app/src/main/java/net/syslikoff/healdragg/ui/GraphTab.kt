package net.syslikoff.healdragg.ui

import android.graphics.Paint
import android.graphics.Typeface
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
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
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin

/**
 * Паутина связей на нативном Canvas: цель в центре, чаты кольцом, собеседники —
 * внешним кольцом. Жесты: щипок — зум, перетаскивание — сдвиг, тап — карточка узла.
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
    val positions = remember(tgId) { mutableListOf<Pair<GraphNode, Offset>>() }

    LaunchedEffect(tgId) {
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

        val peers = graph.nodes.count { it.type == "peer" }
        Row(
            Modifier.fillMaxWidth().padding(bottom = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Chip("цель", Hd.Accent, icon = Icons.Filled.GpsFixed)
            Chip("$peers собеседников", Hd.Good, icon = Icons.Filled.People)
            Spacer(Modifier.weight(1f))
            TextButton(onClick = {
                scale = 1f
                pan = Offset.Zero
                selected = null
            }) { Text("Сброс", color = Hd.Muted, style = MaterialTheme.typography.labelMedium) }
        }

        if (peers == 0) {
            EmptyState(
                emoji = "",
                icon = Icons.Filled.Share,
                title = "Паутина пуста",
                text = "Здесь появятся люди, которым цель отвечала или которых упоминала. Данные зависят от доступной истории чатов.",
            )
            return@Column
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
                pan += panChange
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
                        }
                    },
            ) {
                val center = Offset(size.width / 2f, size.height / 2f)
                val radiusBase = min(size.width, size.height) * 0.5f
                fun screen(world: Offset): Offset = center + world * scale + pan

                val targetNode = graph.nodes.firstOrNull { it.type == "target" }
                val peerNodes = graph.nodes.filter { it.type == "peer" }
                    .sortedByDescending { it.weight }.take(72)

                val peerRing = radiusBase * 0.76f

                val world = LinkedHashMap<String, Offset>()
                world["target"] = Offset.Zero
                peerNodes.forEachIndexed { index, node ->
                    val angle = (2.0 * Math.PI * index / peerNodes.size - Math.PI / 2 + 0.35).toFloat()
                    world[node.id] = Offset(cos(angle) * peerRing, sin(angle) * peerRing)
                }

                positions.clear()
                graph.nodes.forEach { node ->
                    world[node.id]?.let { positions += node to screen(it) }
                }

                // рёбра
                graph.edges.forEach { edge ->
                    val from = world[edge.source] ?: return@forEach
                    val to = world[edge.target] ?: return@forEach
                    val strength = edge.weight.coerceIn(1, 40) / 40f
                    drawLine(
                        color = if (edge.kind == "peer") {
                            Hd.Good.copy(alpha = 0.18f + 0.35f * strength)
                        } else {
                            Hd.Accent2.copy(alpha = 0.14f + 0.28f * strength)
                        },
                        start = screen(from),
                        end = screen(to),
                        strokeWidth = (0.6f + 2.2f * strength) * scale.coerceIn(0.7f, 1.6f),
                    )
                }

                // узлы
                graph.nodes.forEach { node ->
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
                    drawCircle(color, radius, pos)
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
                    "щипок — зум · тяни — сдвиг · тап — узел",
                    style = MaterialTheme.typography.labelSmall,
                    color = Hd.Dim,
                )
            }
        }

        selected?.let { node ->
            Spacer(Modifier.height(10.dp))
            NodeCard(
                node = node,
                onClose = { selected = null },
                onToast = onToast,
                onOpenProfile = { onOpenProfile(node.username?.let { "@$it" } ?: node.tgId.toString()) },
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
                        "target" -> "цель"
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
                m.int("my_messages").takeIf { it > 0 }?.let { add("${it} от цели") }
                m.int("replies").takeIf { it > 0 }?.let { add("${it} ответов") }
                m.int("mentions").takeIf { it > 0 }?.let { add("${it} упоминаний") }
                m.int("weight").takeIf { it > 1 }?.let { add("сила ${it}") }
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
