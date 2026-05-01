package com.sirena.nina.companion.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color

/**
 * Material3 switches pick up [surfaceVariant] / incomplete dark schemes and look like flat grey pills.
 * These colors match brand primary red with clear on/off states.
 */
@Composable
fun SirenaSwitch(
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    val scheme = MaterialTheme.colorScheme
    Switch(
        checked = checked,
        onCheckedChange = onCheckedChange,
        modifier = modifier,
        enabled = enabled,
        colors =
            SwitchDefaults.colors(
                checkedThumbColor = Color.White,
                checkedTrackColor = scheme.primary,
                uncheckedThumbColor = scheme.surface,
                uncheckedBorderColor = scheme.outline,
                uncheckedTrackColor = scheme.surfaceVariant.copy(alpha = 0.85f),
                disabledCheckedThumbColor = scheme.onSurface.copy(alpha = 0.48f),
                disabledCheckedTrackColor = scheme.onSurface.copy(alpha = 0.22f),
                disabledUncheckedThumbColor = scheme.onSurface.copy(alpha = 0.22f),
                disabledUncheckedTrackColor = scheme.surfaceVariant.copy(alpha = 0.5f),
                disabledUncheckedBorderColor = scheme.outline.copy(alpha = 0.35f),
            ),
    )
}
