package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.ui.theme.Charcoal800

/**
 * Mirrors [sirena_ui.widgets.status_bar.StatusBar] —
 * charcoal strip with four signal dots (Bus / Wi‑Fi / Battery / Voice) + optional right label.
 */
@Composable
fun SirenaStatusFooter(
    modifier: Modifier = Modifier,
    busConnected: Boolean,
    wifiOnline: Boolean,
    batteryOk: Boolean,
    voiceReady: Boolean,
    rightLabel: String,
) {
    Row(
        modifier =
            modifier
                .fillMaxWidth()
                .height(28.dp)
                .background(Charcoal800)
                .padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            StatusDot(on = busConnected, activeColor = MaterialTheme.colorScheme.primary)
            StatusDot(on = wifiOnline, activeColor = MaterialTheme.colorScheme.secondary)
            StatusDot(on = batteryOk, activeColor = MaterialTheme.colorScheme.tertiary)
            StatusDot(on = voiceReady, activeColor = MaterialTheme.colorScheme.error)
        }
        Text(
            text = rightLabel,
            style = MaterialTheme.typography.labelSmall,
            color = Color.White.copy(alpha = 0.85f),
        )
    }
}

@Composable
private fun StatusDot(on: Boolean, activeColor: Color) {
    val base = MaterialTheme.colorScheme.outline.copy(alpha = 0.35f)
    Box(
        modifier =
            Modifier
                .size(8.dp)
                .clip(CircleShape)
                .background(if (on) activeColor else base),
    )
}
