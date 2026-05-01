package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.R
import com.sirena.nina.companion.StatusUi
import org.json.JSONObject

/**
 * Mirrors [sirena_ui.screens.home_screen.HomeScreen] —
 * breadcrumb, hero (image + chips + CTAs), quick-action grid, live status strip.
 */
@Composable
fun SirenaHomeScreen(
    caps: JSONObject?,
    statusUi: StatusUi?,
    onNavigate: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val stripItems = buildHomeStatusStrip(statusUi, caps)

    Column(
        modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            "Nina · Home",
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Card(
            modifier = Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
        ) {
            Row(
                Modifier.padding(12.dp).fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Image(
                    painter = painterResource(R.drawable.nina_hero),
                    contentDescription = "Nina",
                    modifier =
                        Modifier
                            .weight(0.38f)
                            .aspectRatio(4f / 3f)
                            .clip(RoundedCornerShape(8.dp)),
                    contentScale = ContentScale.Fit,
                )
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    HeroTitleSubtitle()
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        SirenaPill("Idle")
                        SirenaPill("Torque ON", emphasis = true)
                        SirenaPill("Voice ready")
                    }
                }
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    HeroButtons(onNavigate)
                }
            }
        }

        Text(
            "Quick actions",
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.padding(top = 4.dp),
        )

        val cols = 4
        SIRENA_QUICK_ACTIONS.chunked(cols).forEach { rowItems ->
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                rowItems.forEach { qa ->
                    QuickActionTile(
                        qa,
                        onClick = { onNavigate(qa.navKey) },
                        modifier = Modifier.weight(1f),
                    )
                }
                repeat(cols - rowItems.size) {
                    Spacer(Modifier.weight(1f))
                }
            }
        }

        Text(
            "System overview",
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.SemiBold,
        )
        StatusOverviewStrip(items = stripItems)
    }
}

@Composable
private fun HeroTitleSubtitle() {
    Text(
        "Hi, I'm Nina.",
        style = MaterialTheme.typography.titleLarge,
        fontWeight = FontWeight.Bold,
    )
    Text(
        "Sirena Robotics · ready when you are.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun HeroButtons(onNavigate: (String) -> Unit) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Button(
            onClick = { onNavigate("actions:playback") },
            modifier = Modifier.width(148.dp),
            colors =
                ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.primary,
                ),
        ) {
            Text("Play actions")
        }
        OutlinedButton(
            onClick = { onNavigate("actions:record") },
            modifier = Modifier.width(148.dp),
        ) {
            Text("Record new")
        }
    }
}

private fun buildHomeStatusStrip(status: StatusUi?, caps: JSONObject?): List<StatusStripItem> {
    val ip = status?.ipv4?.takeIf { it.isNotBlank() } ?: "—"
    val role = status?.wifiRole?.trim()?.takeIf { r -> r.isNotEmpty() && !r.equals("null", ignoreCase = true) } ?: ""
    val apLabel = status?.apSsid?.trim()?.takeIf { it.isNotEmpty() && !it.equals("null", ignoreCase = true) }
    val wifi =
        when {
            !status?.activeStaSsid.isNullOrBlank() -> status!!.activeStaSsid!!
            role == "ap" -> "AP (${apLabel ?: "Nina-Setup"})"
            role == "sta" && status?.activeStaSsid.isNullOrBlank() -> "STA (no SSID reported)"
            role == "unknown" -> if (ip != "—") "Mode unknown (linked)" else "—"
            role.isNotEmpty() -> role
            else -> "—"
        }
    val driveBr =
        when (caps?.optBoolean("robot_bridge_enabled")) {
            true -> "On"
            false -> "Off"
            null -> "—"
        }
    val actBr =
        when (caps?.optBoolean("action_bridge_enabled")) {
            true -> "On"
            false -> "Off"
            null -> "—"
        }
    val seen = if (status?.clientSeen == true) "Yes" else "—"
    return listOf(
        StatusStripItem("Jetson", ip),
        StatusStripItem("Wi‑Fi", wifi),
        StatusStripItem("Drive", driveBr),
        StatusStripItem("Play", actBr),
        StatusStripItem("Seen", seen),
    )
}

@Composable
private fun SirenaPill(text: String, emphasis: Boolean = false) {
    Surface(
        shape = RoundedCornerShape(999.dp),
        color =
            if (emphasis) {
                MaterialTheme.colorScheme.primaryContainer
            } else {
                MaterialTheme.colorScheme.surfaceVariant
            },
    ) {
        Text(
            text,
            Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
            style = MaterialTheme.typography.labelSmall,
        )
    }
}

@Composable
private fun QuickActionTile(
    qa: QuickAction,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Card(
        modifier =
            modifier
                .height(88.dp)
                .clickable(onClick = onClick),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(Modifier.padding(12.dp, 8.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(qa.glyph, style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.primary)
            Text(qa.label, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.bodyMedium)
            Text(
                qa.blurb,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun StatusOverviewStrip(items: List<StatusStripItem>) {
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .padding(12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            items.forEach { item ->
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(item.title, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(item.value, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium)
                }
            }
        }
    }
}
