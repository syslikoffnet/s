package net.syslikoff.healdragg.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.border
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.ExperimentalMaterialApi
import androidx.compose.material.pullrefresh.PullRefreshIndicator
import androidx.compose.material.pullrefresh.pullRefresh
import androidx.compose.material.pullrefresh.rememberPullRefreshState
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Карточка: тёмная поверхность с тонкой рамкой, как в панели. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun HdCard(
    modifier: Modifier = Modifier,
    onClick: (() -> Unit)? = null,
    onLongClick: (() -> Unit)? = null,
    contentPadding: androidx.compose.foundation.layout.PaddingValues =
        androidx.compose.foundation.layout.PaddingValues(16.dp),
    content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit,
) {
    val base = Modifier
        .fillMaxWidth()
        .clip(RoundedCornerShape(18.dp))
        .background(Hd.Surface)
        .border(1.dp, Hd.Stroke, RoundedCornerShape(18.dp))
    val interactive = when {
        onLongClick != null -> base.combinedClickable(
            onClick = { onClick?.invoke() },
            onLongClick = onLongClick,
        )
        onClick != null -> base.clickable(onClick = onClick)
        else -> base
    }
    Column(modifier = interactive.padding(contentPadding), content = content)
}

@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text.uppercase(),
        style = SectionTitleStyle,
        color = Hd.Dim,
        modifier = modifier.padding(horizontal = 4.dp, vertical = 6.dp),
    )
}

@Composable
fun Chip(
    text: String,
    color: Color = Hd.Muted,
    modifier: Modifier = Modifier,
    emoji: String? = null,
) {
    Row(
        modifier = modifier
            .clip(RoundedCornerShape(50))
            .background(color.copy(alpha = 0.12f))
            .border(1.dp, color.copy(alpha = 0.35f), RoundedCornerShape(50))
            .padding(horizontal = 9.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (emoji != null) {
            Text(emoji, fontSize = 10.5.sp)
            Spacer(Modifier.width(4.dp))
        }
        Text(
            text = text,
            color = color,
            style = MaterialTheme.typography.labelMedium,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
fun StatTile(
    value: String,
    label: String,
    tint: Color = Hd.Text,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .clip(RoundedCornerShape(16.dp))
            .background(Hd.Surface)
            .border(1.dp, Hd.Stroke, RoundedCornerShape(16.dp))
            .padding(horizontal = 12.dp, vertical = 10.dp),
    ) {
        Text(
            text = value,
            color = tint,
            style = MaterialTheme.typography.titleLarge.copy(fontFamily = FontFamily.Monospace),
            maxLines = 1,
        )
        Text(
            text = label,
            color = Hd.Dim,
            style = MaterialTheme.typography.labelSmall,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

/** Аватар с градиентной рамкой и инициалами. */
@Composable
fun Avatar(name: String, seed: Long, size: Int = 46, modifier: Modifier = Modifier) {
    val tint = Hd.avatarColor(seed)
    val brush = Brush.linearGradient(listOf(tint, Hd.Accent2))
    Box(
        modifier = modifier
            .size(size.dp)
            .clip(CircleShape)
            .background(brush)
            .padding(2.dp)
            .clip(CircleShape)
            .background(Hd.Surface2),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = name.take(2).uppercase(),
            color = Hd.Text,
            fontWeight = FontWeight.Bold,
            fontSize = (size / 3).sp,
            fontFamily = FontFamily.Monospace,
        )
    }
}

@Composable
fun StatusPill(status: String?) {
    val color = Hd.statusColor(status)
    Chip(text = Hd.statusLabel(status), color = color)
}

@Composable
fun EmptyState(
    emoji: String,
    title: String,
    text: String,
    modifier: Modifier = Modifier,
    actionLabel: String? = null,
    onAction: (() -> Unit)? = null,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 24.dp, vertical = 42.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(emoji, fontSize = 40.sp)
        Spacer(Modifier.height(10.dp))
        Text(
            title,
            style = MaterialTheme.typography.titleMedium,
            color = Hd.Text,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(6.dp))
        Text(
            text,
            style = MaterialTheme.typography.bodySmall,
            color = Hd.Muted,
            textAlign = TextAlign.Center,
        )
        if (actionLabel != null && onAction != null) {
            Spacer(Modifier.height(14.dp))
            androidx.compose.material3.Button(
                onClick = onAction,
                colors = androidx.compose.material3.ButtonDefaults.buttonColors(
                    containerColor = Hd.Accent,
                    contentColor = Hd.Black,
                ),
                shape = RoundedCornerShape(14.dp),
                modifier = Modifier.heightIn(min = 46.dp),
            ) { Text(actionLabel, fontWeight = FontWeight.SemiBold) }
        }
    }
}

@Composable
fun ErrorBanner(message: String, onRetry: (() -> Unit)? = null, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(Hd.Bad.copy(alpha = 0.10f))
            .border(1.dp, Hd.Bad.copy(alpha = 0.35f), RoundedCornerShape(14.dp))
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("⚠️", fontSize = 14.sp)
        Spacer(Modifier.width(8.dp))
        Text(
            text = message,
            color = Hd.Bad,
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.weight(1f),
        )
        if (onRetry != null) {
            Spacer(Modifier.width(8.dp))
            Text(
                text = "Повторить",
                color = Hd.Bad,
                style = MaterialTheme.typography.labelLarge,
                modifier = Modifier
                    .clip(RoundedCornerShape(10.dp))
                    .clickable(onClick = onRetry)
                    .padding(horizontal = 8.dp, vertical = 6.dp),
            )
        }
    }
}

@Composable
fun LoadingBlock(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxWidth().padding(40.dp), contentAlignment = Alignment.Center) {
        CircularProgressIndicator(color = Hd.Accent, strokeWidth = 2.dp, modifier = Modifier.size(30.dp))
    }
}

@Composable
fun Dot(color: Color, size: Int = 8, modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .size(size.dp)
            .clip(CircleShape)
            .background(color)
            .semantics { contentDescription = "статус" },
    )
}

@Composable
fun HdTextField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    modifier: Modifier = Modifier,
    hint: String? = null,
    singleLine: Boolean = true,
    minLines: Int = 1,
    isError: Boolean = false,
    keyboard: androidx.compose.ui.text.input.KeyboardType = androidx.compose.ui.text.input.KeyboardType.Text,
    visualTransformation: androidx.compose.ui.text.input.VisualTransformation =
        androidx.compose.ui.text.input.VisualTransformation.None,
    trailing: (@Composable () -> Unit)? = null,
) {
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(label, style = MaterialTheme.typography.labelMedium) },
        placeholder = hint?.let { { Text(it, color = Hd.Dim, style = MaterialTheme.typography.bodySmall) } },
        singleLine = singleLine,
        minLines = minLines,
        isError = isError,
        trailingIcon = trailing,
        visualTransformation = visualTransformation,
        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = keyboard),
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = Hd.Accent,
            unfocusedBorderColor = Hd.Stroke,
            focusedLabelColor = Hd.Accent,
            unfocusedLabelColor = Hd.Muted,
            cursorColor = Hd.Accent,
            focusedTextColor = Hd.Text,
            unfocusedTextColor = Hd.Text,
            errorBorderColor = Hd.Bad,
            focusedContainerColor = Hd.Surface,
            unfocusedContainerColor = Hd.Surface,
        ),
        shape = RoundedCornerShape(14.dp),
        modifier = modifier.fillMaxWidth(),
    )
}

/** Pull-to-refresh: M2-индикатор, потому что он стабилен и предсказуем. */
@OptIn(ExperimentalMaterialApi::class)
@Composable
fun RefreshableBox(
    refreshing: Boolean,
    onRefresh: () -> Unit,
    modifier: Modifier = Modifier,
    content: @Composable BoxScope.() -> Unit,
) {
    val state = rememberPullRefreshState(refreshing, onRefresh)
    Box(modifier.pullRefresh(state)) {
        content()
        PullRefreshIndicator(
            refreshing = refreshing,
            state = state,
            modifier = Modifier.align(Alignment.TopCenter),
            backgroundColor = Hd.Surface,
            contentColor = Hd.Accent,
        )
    }
}

@Composable
fun KeyValueRow(label: String, value: String, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier.fillMaxWidth().padding(vertical = 4.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, color = Hd.Muted, style = MaterialTheme.typography.bodySmall)
        Text(
            value,
            color = Hd.Text,
            style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}
