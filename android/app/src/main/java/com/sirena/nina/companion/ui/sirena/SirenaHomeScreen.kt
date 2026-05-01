package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
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
import androidx.compose.material3.HorizontalDivider
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
import androidx.compose.material3.TextButton
import com.sirena.nina.companion.R
import com.sirena.nina.companion.StatusUi
import org.json.JSONObject

/**
 * Mirrors [sirena_ui.screens.home_screen.HomeScreen] —
 * breadcrumb, hero (image + chips + CTAs), quick-action grid, daemon summary, live status strip.
 */
@Composable
fun SirenaHomeScreen(
    caps: JSONObject?,
    capsErr: String?,
    daemonUrl: String?,
    statusUi: StatusUi?,
    onNavigate: (String) -> Unit,
    onSessionClaim: () -> Unit = {},
    onSessionRelease: () -> Unit = {},
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
                Modifier.padding(12.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Image(
                    painter = painterResource(R.drawable.nina_hero),
                    contentDescription = "Nina",
                    modifier =
                        Modifier
                            .width(140.dp)
                            .height(110.dp)
                            .clip(RoundedCornerShape(8.dp)),
                    contentScale = ContentScale.Crop,
                )
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
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

        DaemonLinkSection(
            caps = caps,
            capsErr = capsErr,
            daemonUrl = daemonUrl,
            onSessionClaim = onSessionClaim,
            onSessionRelease = onSessionRelease,
        )

        Text(
            "System overview",
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.SemiBold,
        )
        StatusOverviewStrip(items = stripItems)
    }
}

@Composable
private fun DaemonLinkSection(
    caps: JSONObject?,
    capsErr: String?,
    daemonUrl: String?,
    onSessionClaim: () -> Unit,
    onSessionRelease: () -> Unit,
) {
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f)),
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("Daemon link", fontWeight = FontWeight.SemiBold)
            KeyValueRow(
                label = "Companion URL",
                value = daemonUrl?.trimEnd('/') ?: "Not connected",
                valueEmphasis = true,
            )
            HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.25f))
            when {
                capsErr != null ->
                    Text(capsErr, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)

                caps != null -> {
                    val driveOn = caps.optBoolean("robot_bridge_enabled")
                    val playOn = caps.optBoolean("action_bridge_enabled")
                    val recOn = caps.optBoolean("record_bridge_enabled")
                    val visOn = caps.optBoolean("vision_bridge_enabled")
                    val staticOn = caps.optBoolean("actions_static_enabled")
                    val sessScript = caps.optBoolean("session_script_configured")
                    KeyValueRow("HTTP drive", if (driveOn) "Enabled on Jetson" else "Off — set NINA_LINK_ENABLE_ROBOT_BRIDGE")
                    KeyValueRow(
                        "Motion play",
                        if (playOn) "Enabled on Jetson" else "Off — set NINA_LINK_ENABLE_ACTION_BRIDGE",
                    )
                    KeyValueRow(
                        "Record HTTP",
                        if (recOn) "Enabled on Jetson" else "Off — set NINA_LINK_ENABLE_RECORD_BRIDGE",
                    )
                    KeyValueRow(
                        "Vision stream",
                        if (visOn) "Enabled on Jetson" else "Off — set NINA_LINK_ENABLE_VISION_BRIDGE",
                    )
                    KeyValueRow(
                        "Audio/media HTTP",
                        if (staticOn) "Enabled on Jetson" else "Off — set NINA_LINK_ENABLE_ACTIONS_STATIC",
                    )
                    KeyValueRow(
                        "Session helper",
                        if (sessScript) "Script configured (claim/release)" else "Off — set NINA_LINK_SESSION_SCRIPT",
                    )
                    caps.optString("drive_endpoint").takeIf { it.isNotBlank() }?.let {
                        KeyValueRow("Drive path", it)
                    }
                    caps.optString("actions_endpoint").takeIf { it.isNotBlank() }?.let {
                        KeyValueRow("Actions list", it)
                    }
                    caps.optString("action_play_endpoint").takeIf { it.isNotBlank() }?.let {
                        KeyValueRow("Play endpoint", it)
                    }
                    caps.optString("manifest_path").takeIf { it.isNotBlank() }?.let {
                        KeyValueRow("Manifest", it, singleLine = false)
                    }
                    val pulse = caps.optInt("default_duration_ms").takeIf { it > 0 }
                    val speed = caps.optInt("default_speed_percent").takeIf { it > 0 }
                    if (pulse != null || speed != null) {
                        KeyValueRow(
                            "Drive defaults",
                            listOfNotNull(
                                pulse?.let { "pulse ${it} ms" },
                                speed?.let { "speed ${it}%" },
                            ).joinToString(" · "),
                        )
                    }
                    caps.optString("message").takeIf { it.isNotBlank() }?.let { msg ->
                        Text(
                            msg,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    if (sessScript) {
                        Row(
                            Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                        ) {
                            TextButton(onClick = onSessionClaim) {
                                Text("Session: claim Jetson UI")
                            }
                            TextButton(onClick = onSessionRelease) {
                                Text("Session: release")
                            }
                        }
                    }
                }

                daemonUrl.isNullOrBlank() -> Text("Open the main dashboard and refresh to reach the Jetson.")
                else -> Text("Loading capabilities…", style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun KeyValueRow(
    label: String,
    value: String,
    valueEmphasis: Boolean = false,
    singleLine: Boolean = true,
) {
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(
            label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            value,
            style =
                if (valueEmphasis) {
                    MaterialTheme.typography.bodyMedium
                } else {
                    MaterialTheme.typography.bodySmall
                },
            fontWeight = if (valueEmphasis) FontWeight.Medium else FontWeight.Normal,
            maxLines = if (singleLine) 3 else 12,
        )
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
