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
    secondary = BrandCharcoal,
    onSecondary = Color.White,
    background = BrandCloud,
    onBackground = BrandText,
    surface = Color.White,
    onSurface = BrandText,
    surfaceVariant = Color(0xFFE3E3E6),
    onSurfaceVariant = BrandMuted,
    outline = Color(0xFFE3E3E6),
)

private val DarkColors = darkColorScheme(
    primary = BrandRed,
    onPrimary = Color.White,
    secondary = BrandCharcoal,
    onSecondary = Color.White,
    background = BrandCharcoal,
    onBackground = Color.White,
    surface = Color(0xFF3A3A3C),
    onSurface = Color.White,
)

@Composable
fun SirenaTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    MaterialTheme(
        colorScheme = if (dark) DarkColors else LightColors,
        content = content,
    )
}
