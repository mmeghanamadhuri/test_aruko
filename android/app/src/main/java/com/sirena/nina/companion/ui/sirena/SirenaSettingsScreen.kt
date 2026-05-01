package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.StatusUi
import org.json.JSONObject

/**
 * Category rail like desktop Settings — Jetson-side options are documented here;
 * Wi‑Fi / pairing / bearer token live under the main Setup flow.
 */
@Composable
fun SirenaSettingsScreen(
    selectedCategoryKey: String,
    onCategorySelected: (String) -> Unit,
    daemonUrl: String?,
    caps: JSONObject?,
    statusUi: StatusUi?,
    modifier: Modifier = Modifier,
) {
    Row(modifier.fillMaxSize()) {
        NavigationRail(
            modifier = Modifier.fillMaxHeight(),
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
        ) {
            SIRENA_SETTINGS_CATEGORIES.forEach { cat ->
                NavigationRailItem(
                    selected = selectedCategoryKey == cat.key,
                    onClick = { onCategorySelected(cat.key) },
                    icon = { Text(cat.glyph) },
                    label = {
                        Text(
                            cat.label,
                            style = MaterialTheme.typography.labelSmall,
                            maxLines = 2,
                        )
                    },
                )
            }
        }
        Column(
            Modifier
                .weight(1f)
                .fillMaxHeight()
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            val cat =
                SIRENA_SETTINGS_CATEGORIES.find { it.key == selectedCategoryKey }
                    ?: SIRENA_SETTINGS_CATEGORIES.first()
            Text(
                "Nina · Settings · ${cat.label}",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(cat.label, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)

            when (cat.key) {
                "general" -> {
                    Text(
                        "Robot name, locale, and motion defaults are edited in Sirena UI on the Jetson " +
                            "(same repo as ``nina/actions/manifest.json``).",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    caps?.optString("manifest_path")?.takeIf { it.isNotBlank() }?.let { mp ->
                        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                Text("Manifest path (Jetson)", fontWeight = FontWeight.SemiBold)
                                Text(mp, style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    }
                    DaemonLinkCard(daemonUrl)
                }

                "network" -> {
                    Text(
                        "Saved Wi‑Fi profiles, hotspot mode, and pairing PIN are managed in the Setup tab " +
                            "(``POST /v1/pair``, saved networks on the daemon).",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    statusUi?.let { s ->
                        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text("Current link snapshot", fontWeight = FontWeight.SemiBold)
                                KeyValueRow("Wi‑Fi role", s.wifiRole)
                                KeyValueRow("IPv4", s.ipv4 ?: "—")
                                KeyValueRow("User mode", s.userMode)
                                s.activeStaSsid?.takeIf { it.isNotBlank() }?.let {
                                    KeyValueRow("STA SSID", it)
                                }
                            }
                        }
                    }
                    DaemonLinkCard(daemonUrl)
                }

                "display", "audio", "privacy", "autodock", "voice", "power", "ota" -> {
                    Text(
                        categoryBlurb(cat.key),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.4f))) {
                        Text(
                            "These categories mirror the desktop Settings stack. Values live on the Jetson " +
                                "until companion APIs expose them.",
                            Modifier.padding(16.dp),
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                }

                else -> {
                    Text(
                        "Use Sirena UI on the robot for this category.",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
        }
    }
}

@Composable
private fun DaemonLinkCard(daemonUrl: String?) {
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Companion daemon URL", fontWeight = FontWeight.SemiBold)
            Text(
                daemonUrl?.trim()?.ifBlank { null } ?: "Not connected — open Setup.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun KeyValueRow(k: String, v: String) {
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(k, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(v, style = MaterialTheme.typography.bodyMedium)
    }
}

private fun categoryBlurb(key: String): String =
    when (key) {
        "display" ->
            "Theme, density, and fullscreen options apply to Sirena UI on the desktop."
        "audio" ->
            "Speaker routing and playback volume are configured on the Jetson (alsa / Pulse); companion streams clips via HTTP when static media is enabled."
        "privacy" ->
            "Telemetry and logging preferences are robot-side in Sirena UI."
        "autodock" ->
            "Dock targets and approach speeds are defined in the navigation stack on the Jetson."
        "voice" ->
            "Wake word and cloud voice settings are part of the ESP / voice pipeline on the robot."
        "power" ->
            "Sleep policies and battery thresholds are configured in Sirena UI or system power settings on the Jetson."
        "ota" ->
            "Over-the-air updates are issued from the Jetson image / release channel — not from the tablet."
        else -> "Configured on the Jetson."
    }
