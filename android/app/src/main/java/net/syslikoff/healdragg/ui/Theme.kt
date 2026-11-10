package net.syslikoff.healdragg.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
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

/** Две темы: строгая Material 3 и «компактная» моно-палитра прежних версий. */
enum class HdMode { Material, Compact }

private class Palette(
    val bg: Color,
    val surface: Color,
    val surface2: Color,
    val stroke: Color,
    val accent: Color,
    val accent2: Color,
    val good: Color,
    val warn: Color,
    val bad: Color,
    val text: Color,
    val muted: Color,
    val dim: Color,
    val onAccent: Color,
)

private val MaterialPalette = Palette(
    bg = Color(0xFF101014),
    surface = Color(0xFF18191F),
    surface2 = Color(0xFF22232B),
    stroke = Color(0xFF3A3B45),
    accent = Color(0xFFD0BCFF),
    accent2 = Color(0xFF9CDCF0),
    good = Color(0xFF8DDBA9),
    warn = Color(0xFFFFC98A),
    bad = Color(0xFFFFB4AB),
    text = Color(0xFFE8E2EC),
    muted = Color(0xFFC8C3D1),
    dim = Color(0xFF938F9E),
    onAccent = Color(0xFF2B1E4E),
)

private val CompactPalette = Palette(
    bg = Color(0xFF05070D),
    surface = Color(0xFF0B1220),
    surface2 = Color(0xFF0E1626),
    stroke = Color(0xFF1D2B44),
    accent = Color(0xFF7C5CFF),
    accent2 = Color(0xFF22D3EE),
    good = Color(0xFF6EE7A8),
    warn = Color(0xFFFFB86B),
    bad = Color(0xFFFF6B81),
    text = Color(0xFFE8F0FF),
    muted = Color(0xFF8BA1BF),
    dim = Color(0xFF5C7290),
    onAccent = Color(0xFF160F26),
)

/**
 * Палитра интерфейса. Значения читаются в момент композиции, поэтому переключение
 * режима в настройках применяется ко всему приложению сразу, без перезапуска.
 */
object Hd {
    var mode: HdMode = HdMode.Material

    val material3: Boolean
        get() = mode == HdMode.Material

    private val p: Palette
        get() = if (material3) MaterialPalette else CompactPalette

    val Bg: Color get() = p.bg
    val Surface: Color get() = p.surface
    val Surface2: Color get() = p.surface2
    val Stroke: Color get() = p.stroke
    val Accent: Color get() = p.accent
    val Accent2: Color get() = p.accent2
    val Good: Color get() = p.good
    val Warn: Color get() = p.warn
    val Bad: Color get() = p.bad
    val Text: Color get() = p.text
    val Muted: Color get() = p.muted
    val Dim: Color get() = p.dim
    val Black: Color get() = p.onAccent

    /** Стабильный цвет аватара по tg_id. */
    fun avatarColor(seed: Long): Color {
        val palette = listOf(
            Accent, Accent2, Good, Warn,
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

private fun hdScheme(material: Boolean) = if (material) {
    darkColorScheme(
        primary = MaterialPalette.accent,
        onPrimary = MaterialPalette.onAccent,
        primaryContainer = Color(0xFF3A2C60),
        onPrimaryContainer = Color(0xFFEADDFF),
        secondary = MaterialPalette.accent2,
        onSecondary = Color(0xFF00363F),
        secondaryContainer = Color(0xFF1B3A44),
        onSecondaryContainer = Color(0xFFCDEBF5),
        tertiary = MaterialPalette.good,
        onTertiary = Color(0xFF06301A),
        background = MaterialPalette.bg,
        onBackground = MaterialPalette.text,
        surface = MaterialPalette.bg,
        onSurface = MaterialPalette.text,
        surfaceVariant = MaterialPalette.surface2,
        onSurfaceVariant = MaterialPalette.muted,
        outline = MaterialPalette.stroke,
        outlineVariant = Color(0xFF2A2B33),
        error = MaterialPalette.bad,
        onError = Color(0xFF3F0305),
        errorContainer = Color(0xFF4A1D20),
        onErrorContainer = Color(0xFFFFDAD6),
        scrim = Color(0xCC000000),
    )
} else {
    darkColorScheme(
        primary = CompactPalette.accent,
        onPrimary = CompactPalette.onAccent,
        primaryContainer = CompactPalette.accent.copy(alpha = 0.18f),
        onPrimaryContainer = CompactPalette.text,
        secondary = CompactPalette.accent2,
        onSecondary = CompactPalette.onAccent,
        secondaryContainer = CompactPalette.accent2.copy(alpha = 0.16f),
        onSecondaryContainer = CompactPalette.text,
        tertiary = CompactPalette.good,
        background = CompactPalette.bg,
        onBackground = CompactPalette.text,
        surface = CompactPalette.surface,
        onSurface = CompactPalette.text,
        surfaceVariant = CompactPalette.surface2,
        onSurfaceVariant = CompactPalette.muted,
        outline = CompactPalette.stroke,
        outlineVariant = CompactPalette.stroke,
        error = CompactPalette.bad,
        onError = CompactPalette.text,
        errorContainer = CompactPalette.bad.copy(alpha = 0.16f),
        onErrorContainer = CompactPalette.bad,
        scrim = Color(0xCC000000),
    )
}

private fun hdTypography(material: Boolean): Typography {
    val mono = if (material) FontFamily.SansSerif else FontFamily.Monospace
    return Typography(
        displaySmall = TextStyle(
            fontFamily = FontFamily.SansSerif,
            fontWeight = FontWeight.Bold,
            fontSize = if (material) 32.sp else 30.sp,
            lineHeight = if (material) 40.sp else 36.sp,
            letterSpacing = if (material) 0.sp else (-0.5).sp,
        ),
        headlineSmall = TextStyle(
            fontFamily = FontFamily.SansSerif,
            fontWeight = if (material) FontWeight.Bold else FontWeight.SemiBold,
            fontSize = if (material) 23.sp else 22.sp,
            lineHeight = 29.sp,
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
            fontWeight = if (material) FontWeight.SemiBold else FontWeight.Medium,
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
            fontFamily = mono,
            fontWeight = if (material) FontWeight.Medium else FontWeight.Normal,
            fontSize = 12.sp,
            letterSpacing = 0.2.sp,
        ),
        labelSmall = TextStyle(
            fontFamily = mono,
            fontSize = 10.5.sp,
            letterSpacing = if (material) 0.2.sp else 0.6.sp,
        ),
    )
}

@Composable
fun HealdraggTheme(md3: Boolean = true, content: @Composable () -> Unit) {
    // Режим читается всеми компонентами через Hd, поэтому тумблер меняет вид сразу.
    Hd.mode = if (md3) HdMode.Material else HdMode.Compact
    MaterialTheme(
        colorScheme = hdScheme(Hd.material3),
        typography = hdTypography(Hd.material3),
        shapes = Shapes(
            extraSmall = androidx.compose.foundation.shape.RoundedCornerShape(8.dp),
            small = androidx.compose.foundation.shape.RoundedCornerShape(12.dp),
            medium = androidx.compose.foundation.shape.RoundedCornerShape(16.dp),
            large = androidx.compose.foundation.shape.RoundedCornerShape(20.dp),
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
