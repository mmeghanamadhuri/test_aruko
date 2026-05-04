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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.R
import com.sirena.nina.companion.StatusUi
import com.sirena.nina.companion.CompanionViewModel
import kotlinx.coroutines.delay
import org.json.JSONObject

/**
 * Mirrors [sirena_ui.screens.home_screen.HomeScreen] —
 * breadcrumb, hero (image + chips + CTAs), quick-action grid, live status strip.
 */
@Composable
fun SirenaHomeScreen(
    vm: CompanionViewModel,
    caps: JSONObject?,
    statusUi: StatusUi?,
    onNavigate: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var robotHealth by remember { mutableStateOf<JSONObject?>(null) }
    LaunchedEffect(Unit) {
        while (true) {
            robotHealth =
                try {
                    vm.fetchRobotHealth()
                } catch (_: Exception) {
                    null
                }
            delay(5000)
        }
    }
    val stripItems = buildDesktopSystemOverview(statusUi, caps, robotHealth)

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
                        SirenaPill(heroTorqueLabel(caps), emphasis = true)
                        SirenaPill(heroVoiceLabel(caps))
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

        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "System overview",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
            )
            Text(
                "Tap Health for details",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        StatusOverviewStrip(items = stripItems)
    }
}

private fun heroTorqueLabel(caps: JSONObject?): String =
    when (caps?.optBoolean("robot_bridge_enabled")) {
        true -> "Torque ON"
        false -> "Drive bridge off"
        null -> "Torque …"
    }

private fun heroVoiceLabel(caps: JSONObject?): String =
    when (caps?.optBoolean("vision_bridge_enabled")) {
        true -> "Voice ready"
        false -> "Voice off"
        null -> "Voice …"
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

private fun healthRowDetail(health: JSONObject?, key: String): String? {
    val rows = health?.optJSONArray("rows") ?: return null
    for (i in 0 until rows.length()) {
        val o = rows.optJSONObject(i) ?: continue
        if (o.optString("key", "") == key) {
            return o.optString("detail").takeIf { it.isNotBlank() }
        }
    }
    return null
}

/** Matches desktop Home status strip: Bus, Camera, Lidar, Battery, Wi‑Fi. */
private fun buildDesktopSystemOverview(
    status: StatusUi?,
    caps: JSONObject?,
    robotHealth: JSONObject?,
): List<StatusStripItem> {
    val ip = status?.ipv4?.takeIf { it.isNotBlank() } ?: "—"
    val role = status?.wifiRole?.trim()?.takeIf { r -> r.isNotEmpty() && !r.equals("null", ignoreCase = true) } ?: ""
    val apLabel = status?.apSsid?.trim()?.takeIf { it.isNotEmpty() && !it.equals("null", ignoreCase = true) }
    val wifi =
        when {
            !status?.activeStaSsid.isNullOrBlank() -> status!!.activeStaSsid!!
            role == "ap" -> "AP (${apLabel ?: "Nina-Setup"})"
            role == "sta" && status?.activeStaSsid.isNullOrBlank() -> "STA (no SSID)"
            role == "unknown" -> if (ip != "—") "Linked" else "—"
            role.isNotEmpty() -> role
            else -> "Online"
        }

    val bus =
        when (caps?.optBoolean("robot_bridge_enabled")) {
            true -> "Ready"
            false -> "Off"
            null -> "…"
        }

    val camera =
        healthRowDetail(robotHealth, "camera")
            ?: when (caps?.optBoolean("vision_bridge_enabled")) {
                true -> "Bridge on"
                false -> "Off"
                null -> "…"
            }

    val lidar =
        healthRowDetail(robotHealth, "lidar")
            ?: healthRowDetail(robotHealth, "slam")
                ?: when (caps?.optBoolean("slam_bridge_enabled")) {
                    true -> "Bridge on"
                    false -> "Off"
                    null -> "…"
                }

    val battery = "n/a"

    return listOf(
        StatusStripItem("Bus", bus),
        StatusStripItem("Camera", camera.take(24)),
        StatusStripItem("Lidar", lidar.take(24)),
        StatusStripItem("Battery", battery),
        StatusStripItem("Wi‑Fi", wifi.take(24)),
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
