package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.StatusUi
import kotlinx.coroutines.delay
import org.json.JSONArray
import org.json.JSONObject

/**
 * Nina Link liveness (`GET /health`), bridge flags, and aggregated subsystem rows
 * (`GET /v1/robot/health`) matching desktop health collector intent via nina-link bridges.
 */
@Composable
fun SirenaHealthScreen(
    vm: CompanionViewModel,
    daemonUrl: String?,
    caps: JSONObject?,
    statusUi: StatusUi?,
    modifier: Modifier = Modifier,
) {
    var health by remember { mutableStateOf<JSONObject?>(null) }
    var healthErr by remember { mutableStateOf<String?>(null) }
    var robotHealth by remember { mutableStateOf<JSONObject?>(null) }

    LaunchedEffect(daemonUrl) {
        if (daemonUrl.isNullOrBlank()) {
            health = null
            robotHealth = null
            healthErr = "No daemon URL — complete Setup first."
            return@LaunchedEffect
        }
        healthErr = null
        health =
            try {
                vm.fetchDaemonHealth()
            } catch (_: Exception) {
                null
            }
        if (health == null) {
            healthErr = "Could not reach GET /health on the Jetson."
        }
    }

    LaunchedEffect(daemonUrl) {
        if (daemonUrl.isNullOrBlank()) return@LaunchedEffect
        while (true) {
            robotHealth = vm.fetchRobotHealth()
            delay(3000)
        }
    }

    Column(
        modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            "Nina · Health",
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Text(
            "Daemon process + bridge flags + live subsystem rows from the Jetson (same stacks as " +
                "the desktop app when bridges are enabled).",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Card(
            Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        ) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Nina Link daemon", fontWeight = FontWeight.Bold)
                healthErr?.let {
                    Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                }
                health?.let { j ->
                    val ok = j.optBoolean("ok")
                    Row(
                        Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("GET /health", style = MaterialTheme.typography.bodyMedium)
                        HealthStatusChip(ok = ok, label = if (ok) "ok" else "check")
                    }
                    Text(
                        "service: ${j.optString("service", "—")}" +
                            if (j.has("mock_nm")) " · mock_nm=${j.optBoolean("mock_nm")}" else "",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        Text("Robot subsystems", fontWeight = FontWeight.SemiBold)
        robotHealth?.optJSONArray("rows")?.let { arr ->
            SubsystemRows(arr)
        } ?: Text(
            if (daemonUrl.isNullOrBlank()) {
                "—"
            } else {
                "Loading GET /v1/robot/health…"
            },
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        statusUi?.lastError?.takeIf { it.isNotBlank() }?.let { err ->
            Card(
                Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.35f)),
            ) {
                Text(
                    "Last daemon error (status)\n$err",
                    Modifier.padding(16.dp),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onErrorContainer,
                )
            }
        }

        Text("HTTP bridges", fontWeight = FontWeight.SemiBold)
        caps?.let { c ->
            BridgeRow("Robot drive", c.optBoolean("robot_bridge_enabled"))
            BridgeRow("Action playback", c.optBoolean("action_bridge_enabled"))
            BridgeRow("Desktop action delegate", c.optBoolean("action_delegate_configured"))
            BridgeRow("Record session", c.optBoolean("record_bridge_enabled"))
            BridgeRow("Vision / camera", c.optBoolean("vision_bridge_enabled"))
            BridgeRow("SLAM / lidar", c.optBoolean("slam_bridge_enabled"))
            BridgeRow("Depth (RealSense)", c.optBoolean("depth_bridge_enabled"))
            BridgeRow("Autonomy", c.optBoolean("autonomy_bridge_enabled"))
            BridgeRow("Static media (audio files)", c.optBoolean("actions_static_enabled"))
            val manifest = c.optString("manifest_path").takeIf { it.isNotBlank() }
            if (manifest != null) {
                Text(
                    "Manifest: $manifest",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
        } ?: Text("Capabilities not loaded yet.", style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun HealthStatusChip(ok: Boolean, label: String) {
    val color =
        if (ok) {
            MaterialTheme.colorScheme.primaryContainer
        } else {
            MaterialTheme.colorScheme.tertiaryContainer
        }
    Surface(color = color, shape = MaterialTheme.shapes.small) {
        Text(
            label,
            Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
            style = MaterialTheme.typography.labelSmall,
        )
    }
}

@Composable
private fun SubsystemRows(rows: JSONArray) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        for (i in 0 until rows.length()) {
            val o = rows.optJSONObject(i) ?: continue
            val label = o.optString("label", "—")
            val detail = o.optString("detail", "")
            val st = o.optString("status", "pending")
            SubsystemHealthCard(label = label, detail = detail, status = st)
        }
    }
}

@Composable
private fun SubsystemHealthCard(label: String, detail: String, status: String) {
    val chip =
        when (status) {
            "ok" -> "OK" to MaterialTheme.colorScheme.primaryContainer
            "warn" -> "WARN" to MaterialTheme.colorScheme.tertiaryContainer
            "error" -> "ERR" to MaterialTheme.colorScheme.errorContainer
            else -> "—" to MaterialTheme.colorScheme.surfaceVariant
        }
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.25f)),
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(label, fontWeight = FontWeight.Medium, style = MaterialTheme.typography.bodyMedium)
                Surface(color = chip.second, shape = MaterialTheme.shapes.small) {
                    Text(
                        chip.first,
                        Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            }
            if (detail.isNotBlank()) {
                Text(
                    detail,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun BridgeRow(label: String, enabled: Boolean) {
    Card(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.35f)),
    ) {
        Row(
            Modifier.padding(12.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(label, style = MaterialTheme.typography.bodyMedium)
            HealthStatusChip(ok = enabled, label = if (enabled) "on" else "off")
        }
    }
}
