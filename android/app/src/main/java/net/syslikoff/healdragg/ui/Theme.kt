package net.syslikoff.healdragg.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Палитра из веб-панели: тёмный «хакерский» интерфейс, фиолетовый + циан. */
object Hd {
    val Bg = Color(0xFF05070D)
    val Surface = Color(0xFF0B1220)
    val Surface2 = Color(0xFF0E1626)
    val Stroke = Color(0xFF1D2B44)
    val Accent = Color(0xFF7C5CFF)
    val Accent2 = Color(0xFF22D3EE)
    val Good = Color(0xFF6EE7A8)
    val Warn = Color(0xFFFFB86B)
    val Bad = Color(0xFFFF6B81)
    val Text = Color(0xFFE8F0FF)
    val Muted = Color(0xFF8BA1BF)
    val Dim = Color(0xFF5C7290)
    val Black = Color(0xFF160F26)

    /** Стабильный цвет аватара по tg_id. */
    fun avatarColor(seed: Long): Color {
        val palette = listOf(
            Color(0xFF7C5CFF), Color(0xFF22D3EE), Color(0xFF6EE7A8), Color(0xFFFFB86B),
            Color(0xFFFF8FB1), Color(0xFF9AA7FF), Color(0xFF4FD1C5),
        )
        val idx = ((seed % palette.size) + palette.size).toInt() % palette.size
        return palette[idx]
    }

    fun statusColor(status: String?): Color = when (status?.lowercase()) {
        "creator", "admin" -> Accent
        "left", "kicked" -> Bad
        "member" -> Good
        else -> Muted
    }

    fun statusLabel(status: String?): String = when (status?.lowercase()) {
        "creator" -> "владелец"
        "admin" -> "админ"
        "left" -> "вышел"
        "kicked" -> "исключён"
        "member" -> "участник"
        else -> status ?: "—"
    }
}

private val HdScheme = darkColorScheme(
    primary = Hd.Accent,
    onPrimary = Hd.Black,
    primaryContainer = Hd.Accent.copy(alpha = 0.18f),
    onPrimaryContainer = Hd.Text,
    secondary = Hd.Accent2,
    onSecondary = Hd.Black,
    secondaryContainer = Hd.Accent2.copy(alpha = 0.16f),
    onSecondaryContainer = Hd.Text,
    tertiary = Hd.Good,
    background = Hd.Bg,
    onBackground = Hd.Text,
    surface = Hd.Surface,
    onSurface = Hd.Text,
    surfaceVariant = Hd.Surface2,
    onSurfaceVariant = Hd.Muted,
    outline = Hd.Stroke,
    outlineVariant = Hd.Stroke,
    error = Hd.Bad,
    onError = Hd.Text,
    errorContainer = Hd.Bad.copy(alpha = 0.16f),
    onErrorContainer = Hd.Bad,
    scrim = Color(0xCC000000),
)

private val HdTypography = Typography(
    displaySmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Bold,
        fontSize = 30.sp,
        lineHeight = 36.sp,
        letterSpacing = (-0.5).sp,
    ),
    headlineSmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 22.sp,
        lineHeight = 28.sp,
    ),
    titleLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 19.sp,
        lineHeight = 25.sp,
    ),
    titleMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 16.sp,
        lineHeight = 22.sp,
    ),
    titleSmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.Medium,
        fontSize = 14.sp,
        lineHeight = 20.sp,
    ),
    bodyLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontSize = 16.sp,
        lineHeight = 23.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontSize = 14.sp,
        lineHeight = 21.sp,
    ),
    bodySmall = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontSize = 12.5.sp,
        lineHeight = 18.sp,
    ),
    labelLarge = TextStyle(
        fontFamily = FontFamily.SansSerif,
        fontWeight = FontWeight.SemiBold,
        fontSize = 13.sp,
        letterSpacing = 0.2.sp,
    ),
    labelMedium = TextStyle(
        fontFamily = FontFamily.Monospace,
        fontSize = 12.sp,
        letterSpacing = 0.3.sp,
    ),
    labelSmall = TextStyle(
        fontFamily = FontFamily.Monospace,
        fontSize = 10.5.sp,
        letterSpacing = 0.6.sp,
    ),
)

@Composable
fun HealdraggTheme(content: @Composable () -> Unit) {
    // Приложение всегда тёмное: это часть характера, а не «светлая тема по системе»
    @Suppress("UNUSED_VARIABLE")
    val dark = isSystemInDarkTheme()
    MaterialTheme(
        colorScheme = HdScheme,
        typography = HdTypography,
        shapes = androidx.compose.material3.Shapes(
            extraSmall = androidx.compose.foundation.shape.RoundedCornerShape(8.dp),
            small = androidx.compose.foundation.shape.RoundedCornerShape(12.dp),
            medium = androidx.compose.foundation.shape.RoundedCornerShape(16.dp),
            large = androidx.compose.foundation.shape.RoundedCornerShape(22.dp),
            extraLarge = androidx.compose.foundation.shape.RoundedCornerShape(28.dp),
        ),
        content = content,
    )
}

/** Заголовок секции: мелкий капс, разрядка, как в терминале. */
val SectionTitleStyle = TextStyle(
    fontFamily = FontFamily.Monospace,
    fontWeight = FontWeight.Medium,
    fontSize = 11.sp,
    letterSpacing = 1.4.sp,
    textAlign = TextAlign.Start,
)
