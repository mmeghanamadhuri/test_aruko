package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.StatusUi
import org.json.JSONObject

/**
 * Category rail like desktop Settings — each section shows **live** Jetson data from
 * `GET /v1/robot/capabilities` and `GET /v1/status` where available. Use **Setup** for daemon URL / pairing if you prefer the full provisioning layout.
 */
@Composable
fun SirenaSettingsScreen(
    selectedCategoryKey: String,
    onCategorySelected: (String) -> Unit,
    vm: CompanionViewModel,
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
                    SettingsLead(
                        "Manifest and motion registration live on the Jetson; bridges below show what " +
                            "this companion can call over HTTP.",
                    )
                    CapabilitiesBridgesCard(caps)
                    caps?.optString("manifest_path")?.takeIf { it.isNotBlank() }?.let { mp ->
                        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                Text("Manifest path (Jetson)", fontWeight = FontWeight.SemiBold)
                                Text(mp, style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    }
                    SessionScriptCard(caps)
                    JetsonShutdownCard(vm)
                    DaemonLinkCard(daemonUrl)
                }

                "network" -> {
                    SettingsLead(
                        "Network controls are available here for parity with desktop Settings.",
                    )
                    NetworkActionsCard(vm)
                    statusUi?.let { s ->
                        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text("Current link snapshot", fontWeight = FontWeight.SemiBold)
                                KeyValueRow("Paired (session)", if (s.paired) "Yes" else "No")
                                KeyValueRow("Saved profiles", "${s.savedNetworks.size}")
                                KeyValueRow("Wi‑Fi role", s.wifiRole)
                                KeyValueRow("IPv4", s.ipv4 ?: "—")
                                KeyValueRow("User mode", s.userMode)
                                s.activeStaSsid?.takeIf { it.isNotBlank() }?.let {
                                    KeyValueRow("STA SSID", it)
                                }
                                s.apSsid?.takeIf { it.isNotBlank() }?.let {
                                    KeyValueRow("AP SSID (configured)", it)
                                }
                            }
                        }
                    } ?: Text("No status yet — tap Refresh in Setup.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    DaemonLinkCard(daemonUrl)
                }

                "display" -> {
                    SettingsLead(
                        "This companion is landscape-locked. Video streams use the Jetson vision pipeline when enabled.",
                    )
                    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text("Vision (HTTP)", fontWeight = FontWeight.SemiBold)
                            FeatureToggleRow("Vision bridge", caps?.optBoolean("vision_bridge_enabled") == true)
                            caps?.optString("vision_stream_endpoint")?.takeIf { it.isNotBlank() }?.let {
                                KeyValueRow("Stream", it)
                            }
                            caps?.optString("vision_status_endpoint")?.takeIf { it.isNotBlank() }?.let {
                                KeyValueRow("Status", it)
                            }
                        }
                    }
                    CapsMessageCard(caps)
                }

                "audio" -> {
                    SettingsLead(
                        "Playback clips and gTTS use static media and action audio endpoints when enabled on the Jetson.",
                    )
                    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text("Bridges", fontWeight = FontWeight.SemiBold)
                            FeatureToggleRow("Actions static (media files)", caps?.optBoolean("actions_static_enabled") == true)
                            FeatureToggleRow("Record bridge", caps?.optBoolean("record_bridge_enabled") == true)
                            FeatureToggleRow("Action bridge (playback)", caps?.optBoolean("action_bridge_enabled") == true)
                            caps?.optString("action_audio_generate_endpoint")?.takeIf { it.isNotBlank() }?.let {
                                KeyValueRow("Generate audio", it)
                            }
                            caps?.optString("media_file_endpoint")?.takeIf { it.isNotBlank() }?.let {
                                KeyValueRow("Media file", it)
                            }
                        }
                    }
                    CapsMessageCard(caps)
                }

                "privacy" -> {
                    SettingsLead(
                        "Session claim and release coordinate exclusive use with the link daemon. Tablet logs: Setup → Session log.",
                    )
                    SessionControlCard(vm)
                    statusUi?.let { s ->
                        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text("Visibility", fontWeight = FontWeight.SemiBold)
                                KeyValueRow("Daemon saw this client", if (s.clientSeen) "Yes" else "No")
                                KeyValueRow("Paired", if (s.paired) "Yes" else "No")
                            }
                        }
                    }
                }

                "autodock" -> {
                    SettingsLead(
                        "Autonomous docking is configured on the robot; the companion exposes **HTTP drive** settings from nina-link.",
                    )
                    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text("Robot drive (HTTP)", fontWeight = FontWeight.SemiBold)
                            FeatureToggleRow("Robot bridge", caps?.optBoolean("robot_bridge_enabled") == true)
                            caps?.optString("drive")?.takeIf { it.isNotBlank() }?.let {
                                KeyValueRow("Drive mode", it)
                            }
                            caps?.let { c ->
                                if (c.has("default_duration_ms")) {
                                    KeyValueRow("Default pulse (ms)", "${c.optInt("default_duration_ms")}")
                                }
                                if (c.has("default_speed_percent")) {
                                    KeyValueRow("Default speed %", "${c.optInt("default_speed_percent")}")
                                }
                            }
                            caps?.optString("drive_endpoint")?.takeIf { it.isNotBlank() }?.let {
                                KeyValueRow("Endpoint", it)
                            }
                        }
                    }
                    CapsMessageCard(caps)
                }

                "voice" -> {
                    SettingsLead(
                        "Voice clips for actions are generated on the Jetson when **actions static** and **audio generate** are enabled.",
                    )
                    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text("Action audio API", fontWeight = FontWeight.SemiBold)
                            FeatureToggleRow("Static media / audio editor", caps?.optBoolean("actions_static_enabled") == true)
                            caps?.optString("action_audio_generate_endpoint")?.takeIf { it.isNotBlank() }?.let {
                                KeyValueRow("Generate (gTTS)", it)
                            }
                            caps?.optString("action_audio_info_endpoint")?.takeIf { it.isNotBlank() }?.let {
                                KeyValueRow("Info", it)
                            }
                        }
                    }
                    CapsMessageCard(caps)
                }

                "power" -> {
                    SettingsLead("Runtime power and boot policy signals reported by nina-link.")
                    statusUi?.let { s ->
                        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text("Jetson runtime", fontWeight = FontWeight.SemiBold)
                                KeyValueRow("User mode", s.userMode)
                                KeyValueRow("Boot window left (s)", "${s.bootWaitRemainingSec}")
                                s.lastError?.takeIf { it.isNotBlank() }?.let {
                                    Text(
                                        "Last error: $it",
                                        color = MaterialTheme.colorScheme.error,
                                        style = MaterialTheme.typography.bodySmall,
                                    )
                                }
                            }
                        }
                    } ?: Text("No status yet — refresh connection from Setup.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                }

                "ota" -> {
                    SettingsLead(
                        "Robot OS and Jetson image updates are applied on the robot, not through this HTTP API.",
                    )
                    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.35f))) {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text("nina-link daemon", fontWeight = FontWeight.SemiBold)
                            Text(
                                "Use SSH or your release process on the Jetson. The companion only talks to the " +
                                    "link daemon for manifests, bridges, and Wi‑Fi helpers.",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                    CapsMessageCard(caps)
                }

                else -> {
                    Text("Unknown category.", style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
    }
}

@Composable
private fun SettingsLead(text: String) {
    Text(text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

/** Same Jetson Wi‑Fi mutations as Setup — desktop Settings → Network parity for operators already in Nina console. */
@Composable
private fun NetworkActionsCard(vm: CompanionViewModel) {
    var ssid by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var showPassword by remember { mutableStateOf(false) }
    var feedback by remember { mutableStateOf<String?>(null) }
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("Jetson Wi‑Fi actions", fontWeight = FontWeight.SemiBold)
            Text(
                "These calls mirror **Setup** — they change NetworkManager profiles on the robot over HTTP.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedTextField(
                value = ssid,
                onValueChange = { ssid = it },
                label = { Text("Home SSID") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                label = { Text("Password") },
                singleLine = true,
                visualTransformation =
                    if (showPassword) {
                        VisualTransformation.None
                    } else {
                        PasswordVisualTransformation()
                    },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                modifier = Modifier.fillMaxWidth(),
            )
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                TextButton(onClick = { showPassword = !showPassword }) {
                    Text(if (showPassword) "Hide" else "Show")
                }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(
                    onClick = {
                        feedback = null
                        vm.saveHomeAndOptionallyConnect(ssid, password, connect = false)
                        feedback = "Save creds requested — check Setup / status if it failed."
                    },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Save creds")
                }
                Button(
                    onClick = {
                        feedback = null
                        vm.saveHomeAndOptionallyConnect(ssid, password, connect = true)
                        feedback = "Save & connect requested — join the same Wi‑Fi on the tablet when the Jetson moves to STA."
                    },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Save & connect")
                }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(
                    onClick = {
                        feedback = null
                        vm.connectJetsonHome(null)
                    },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Connect saved")
                }
                OutlinedButton(
                    onClick = {
                        feedback = null
                        vm.startApOnJetson()
                    },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Force AP")
                }
            }
            OutlinedButton(
                onClick = {
                    feedback = null
                    vm.refreshStatus()
                    feedback = "Status refresh requested."
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Refresh link status")
            }
            feedback?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary)
            }
        }
    }
}

@Composable
private fun CapabilitiesBridgesCard(caps: JSONObject?) {
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("HTTP bridges (Jetson)", fontWeight = FontWeight.SemiBold)
            if (caps == null) {
                Text(
                    "Connect to the daemon — capabilities load when online.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                return@Column
            }
            FeatureToggleRow("Robot / drive", caps.optBoolean("robot_bridge_enabled"))
            FeatureToggleRow("Action playback", caps.optBoolean("action_bridge_enabled"))
            FeatureToggleRow("Recording", caps.optBoolean("record_bridge_enabled"))
            FeatureToggleRow("Vision", caps.optBoolean("vision_bridge_enabled"))
            FeatureToggleRow("Static media & manifest audio", caps.optBoolean("actions_static_enabled"))
        }
    }
}

@Composable
private fun JetsonShutdownCard(vm: CompanionViewModel) {
    var confirm by remember { mutableStateOf(false) }
    var feedback by remember { mutableStateOf<String?>(null) }
    var feedbackIsError by remember { mutableStateOf(false) }
    if (confirm) {
        AlertDialog(
            onDismissRequest = { confirm = false },
            title = { Text("Shut down Jetson?") },
            text = {
                Text(
                    "The host will run power-off. The robot may stop moving and the link will drop. " +
                        "Mutating API calls need a valid bearer token if the daemon is locked down.",
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirm = false
                        vm.requestJetsonShutdown { err ->
                            feedbackIsError = err != null
                            feedback =
                                err
                                    ?: "Power-off requested — expect disconnect within a few seconds."
                        }
                    },
                ) {
                    Text("Shut down", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = {
                TextButton(onClick = { confirm = false }) { Text("Cancel") }
            },
        )
    }
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Jetson host power-off", fontWeight = FontWeight.SemiBold)
            Text(
                "Calls POST /v1/system/poweroff on nina-link. The service user on the Jetson needs " +
                    "passwordless sudo for poweroff or shutdown (see robot docs).",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedButton(
                onClick = {
                    feedback = null
                    feedbackIsError = false
                    confirm = true
                },
            ) {
                Text("Shut down Jetson…")
            }
            feedback?.let {
                Text(
                    it,
                    style = MaterialTheme.typography.bodySmall,
                    color =
                        if (feedbackIsError) {
                            MaterialTheme.colorScheme.error
                        } else {
                            MaterialTheme.colorScheme.primary
                        },
                )
            }
        }
    }
}

@Composable
private fun SessionScriptCard(caps: JSONObject?) {
    val on = caps?.optBoolean("session_script_configured") == true
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("Session export script", fontWeight = FontWeight.SemiBold)
            Text(
                if (on) {
                    "Configured on the Jetson (`NINA_LINK_SESSION_SCRIPT`). Session logs can be processed by your helper."
                } else {
                    "Not configured — optional shell hook on the Jetson for session artifacts."
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun SessionControlCard(vm: CompanionViewModel) {
    var err by remember { mutableStateOf<String?>(null) }
    var ok by remember { mutableStateOf<String?>(null) }
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("Daemon session", fontWeight = FontWeight.SemiBold)
            Text(
                "Claim before exclusive operations if your deployment uses session locking; release when finished.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = {
                        err = null
                        ok = null
                        vm.sessionClaim { e ->
                            err = e
                            if (e == null) ok = "Session claimed."
                        }
                    },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Claim")
                }
                OutlinedButton(
                    onClick = {
                        err = null
                        ok = null
                        vm.sessionRelease { e ->
                            err = e
                            if (e == null) ok = "Session released."
                        }
                    },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Release")
                }
            }
            ok?.let {
                Text(it, color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.bodySmall)
            }
            err?.let {
                Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
private fun CapsMessageCard(caps: JSONObject?) {
    val msg = caps?.optString("message")?.trim().orEmpty()
    if (msg.isBlank()) return
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.35f))) {
        Text(msg, Modifier.padding(16.dp), style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun FeatureToggleRow(label: String, enabled: Boolean) {
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(label, style = MaterialTheme.typography.bodyMedium)
        Text(
            if (enabled) "On" else "Off",
            fontWeight = FontWeight.Medium,
            color =
                if (enabled) {
                    MaterialTheme.colorScheme.primary
                } else {
                    MaterialTheme.colorScheme.onSurfaceVariant
                },
        )
    }
}

@Composable
private fun DaemonLinkCard(daemonUrl: String?) {
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Companion daemon URL", fontWeight = FontWeight.SemiBold)
            Text(
                daemonUrl?.trim()?.ifBlank { null } ?: "Not connected — set URL in Setup.",
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
