package com.sirena.nina.companion.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// Mirrors sirena_ui/styles.py brand tokens
private val BrandRed = Color(0xFFC8102E)
private val BrandRedDark = Color(0xFF9B0C23)
private val BrandCloud = Color(0xFFF5F5F7)
private val BrandCharcoal = Color(0xFF2C2C2E)
private val BrandText = Color(0xFF1C1C1E)
private val BrandMuted = Color(0xFF6E6E73)

/** Bottom status strip background (mirrors sirena_ui StatusBar charcoal). */
val Charcoal800 = Color(0xFF2C2C2E)

private val LightColors = lightColorScheme(
    primary = BrandRed,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFFBE7EB),
    onPrimaryContainer = BrandRedDark,
    secondary = BrandCharcoal,
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFE8E8ED),
    onSecondaryContainer = BrandCharcoal,
    tertiary = Color(0xFF007AFF),
    onTertiary = Color.White,
    background = BrandCloud,
    onBackground = BrandText,
    surface = Color.White,
    onSurface = BrandText,
    surfaceVariant = Color(0xFFE3E3E6),
    onSurfaceVariant = BrandMuted,
    outline = Color(0xFFC7C7CC),
    outlineVariant = Color(0xFFD1D1D6),
)

private val DarkColors = darkColorScheme(
    primary = BrandRed,
    onPrimary = Color.White,
    primaryContainer = Color(0xFF7A1025),
    onPrimaryContainer = Color(0xFFFFD9DE),
    secondary = Color(0xFF98989D),
    onSecondary = Color(0xFF1C1C1E),
    secondaryContainer = Color(0xFF48484A),
    onSecondaryContainer = Color.White,
    tertiary = Color(0xFF64B5F6),
    onTertiary = Color(0xFF0D2538),
    background = BrandCharcoal,
    onBackground = Color.White,
    surface = Color(0xFF3A3A3C),
    onSurface = Color.White,
    surfaceVariant = Color(0xFF545458),
    onSurfaceVariant = Color(0xFFD8D8DC),
    outline = Color(0xFF636366),
    outlineVariant = Color(0xFF48484A),
)

@Composable
fun SirenaTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    MaterialTheme(
        colorScheme = if (dark) DarkColors else LightColors,
        content = content,
    )
}
