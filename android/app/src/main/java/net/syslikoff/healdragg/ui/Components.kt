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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.HealthAndSafety
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material.pullrefresh.PullRefreshIndicator
import androidx.compose.material.pullrefresh.pullRefresh
import androidx.compose.material.pullrefresh.rememberPullRefreshState
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.vector.ImageVector
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
    val radius = if (Hd.material3) 20.dp else 18.dp
    val shape = RoundedCornerShape(radius)
    val base = Modifier
        .fillMaxWidth()
        .clip(shape)
        .background(if (Hd.material3) Hd.Surface else Hd.Surface)
        .then(if (Hd.material3) Modifier else Modifier.border(1.dp, Hd.Stroke, shape))
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
        style = if (Hd.material3) {
            MaterialTheme.typography.labelLarge.copy(
                fontWeight = FontWeight.SemiBold,
                letterSpacing = 0.6.sp,
            )
        } else {
            SectionTitleStyle
        },
        color = if (Hd.material3) Hd.Muted else Hd.Dim,
        modifier = modifier.padding(horizontal = 4.dp, vertical = 6.dp),
    )
}

@Composable
fun Chip(
    text: String,
    color: Color = Hd.Muted,
    modifier: Modifier = Modifier,
    emoji: String? = null,
    icon: androidx.compose.ui.graphics.vector.ImageVector? = null,
) {
    val shape = RoundedCornerShape(50)
    Row(
        modifier = modifier
            .clip(shape)
            .background(color.copy(alpha = if (Hd.material3) 0.16f else 0.12f))
            .then(
                if (Hd.material3) Modifier
                else Modifier.border(1.dp, color.copy(alpha = 0.35f), shape),
            )
            .padding(horizontal = if (Hd.material3) 10.dp else 9.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (icon != null) {
            Icon(icon, contentDescription = null, tint = color, modifier = Modifier.size(12.dp))
            Spacer(Modifier.width(4.dp))
        } else if (emoji != null) {
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
    val shape = RoundedCornerShape(if (Hd.material3) 18.dp else 16.dp)
    Column(
        modifier = modifier
            .clip(shape)
            .background(Hd.Surface)
            .then(if (Hd.material3) Modifier else Modifier.border(1.dp, Hd.Stroke, shape))
            .padding(horizontal = 12.dp, vertical = if (Hd.material3) 12.dp else 10.dp),
    ) {
        Text(
            text = value,
            color = tint,
            style = if (Hd.material3) {
                MaterialTheme.typography.titleLarge.copy(fontWeight = FontWeight.Bold)
            } else {
                MaterialTheme.typography.titleLarge.copy(fontFamily = FontFamily.Monospace)
            },
            maxLines = 1,
        )
        Text(
            text = label,
            color = Hd.Muted,
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
    if (Hd.material3) {
        Box(
            modifier = modifier
                .size(size.dp)
                .clip(CircleShape)
                .background(tint.copy(alpha = 0.20f)),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                text = name.take(2).uppercase(),
                color = tint,
                fontWeight = FontWeight.Bold,
                fontSize = (size / 3).sp,
            )
        }
        return
    }
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
    icon: ImageVector? = null,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 24.dp, vertical = 42.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        if (icon != null) {
            Box(
                Modifier
                    .size(64.dp)
                    .clip(CircleShape)
                    .background(Hd.Accent.copy(alpha = 0.12f)),
                contentAlignment = Alignment.Center,
            ) {
                Icon(icon, contentDescription = null, tint = Hd.Accent, modifier = Modifier.size(30.dp))
            }
        } else {
            Text(emoji, fontSize = 40.sp)
        }
        Spacer(Modifier.height(14.dp))
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
        Icon(
            Icons.Filled.Warning,
            contentDescription = null,
            tint = Hd.Bad,
            modifier = Modifier.size(16.dp),
        )
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
        shape = RoundedCornerShape(if (Hd.material3) 16.dp else 14.dp),
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

/** Знак приложения: медицинский щит с пульсом — без эмодзи и стоковых картинок. */
@Composable
fun BrandMark(size: Int = 36, modifier: Modifier = Modifier) {
    val shape = RoundedCornerShape(if (Hd.material3) (size * 0.32f).dp else 14.dp)
    Box(
        modifier = modifier
            .size(size.dp)
            .clip(shape)
            .background(
                if (Hd.material3) Hd.Accent else Hd.Accent.copy(alpha = 0.16f),
            ),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            Icons.Filled.HealthAndSafety,
            contentDescription = "Healdragg",
            tint = if (Hd.material3) Hd.Black else Hd.Accent,
            modifier = Modifier.size((size * 0.62f).dp),
        )
    }
}
