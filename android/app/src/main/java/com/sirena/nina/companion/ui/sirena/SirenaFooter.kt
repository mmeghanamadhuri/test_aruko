package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.ui.theme.Charcoal800

/** Mirrors [sirena_ui.widgets.status_bar.StatusBar] dot colours — ok / warn / bad. */
private val DotOk = Color(0xFF2ECC71)
private val DotWarn = Color(0xFFF5A623)
private val DotBad = Color(0xFFE74C3C)

/**
 * Charcoal strip with **Bus / Wi‑Fi / Battery / Voice** — dot + label per subsystem,
 * plus optional right caption (mirrors desktop footer layout).
 */
@Composable
fun SirenaStatusFooter(
    modifier: Modifier = Modifier,
    busOk: Boolean,
    busWarn: Boolean = false,
    wifiOk: Boolean,
    wifiWarn: Boolean = false,
    batteryOk: Boolean,
    batteryWarn: Boolean = false,
    voiceOk: Boolean,
    voiceWarn: Boolean = false,
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
            horizontalArrangement = Arrangement.spacedBy(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            StatusDotWithLabel("Bus", busOk, busWarn)
            StatusDotWithLabel("Wi‑Fi", wifiOk, wifiWarn)
            StatusDotWithLabel("Battery", batteryOk, batteryWarn)
            StatusDotWithLabel("Voice", voiceOk, voiceWarn)
        }
        Text(
            text = rightLabel,
            style = MaterialTheme.typography.labelSmall,
            color = Color(0xFFC7C7CC),
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.widthIn(max = 180.dp),
        )
    }
}

@Composable
private fun StatusDotWithLabel(
    title: String,
    ok: Boolean,
    warn: Boolean,
) {
    Row(
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        val dot =
            when {
                warn -> DotWarn
                ok -> DotOk
                else -> DotBad
            }
        Box(
            modifier =
                Modifier
                    .size(10.dp)
                    .clip(CircleShape)
                    .background(dot),
        )
        Text(
            title,
            style = MaterialTheme.typography.labelSmall,
            color = Color.White,
        )
    }
}
