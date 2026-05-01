package com.sirena.nina.companion.ui.sirena

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.util.NinaLog
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * Mirrors [sirena_ui.screens.drive_screen.DriveScreen] —
 * breadcrumb row, camera card (~55%) + control card (~45%), HUD row, speed slider, D-pad, E-stop.
 */
@Composable
fun SirenaDriveScreen(vm: CompanionViewModel, caps: JSONObject?) {
    val scope = rememberCoroutineScope()
    var actionErr by remember { mutableStateOf<String?>(null) }
    val bridgeOn = caps?.optBoolean("robot_bridge_enabled") == true
    val defaultMs = caps?.optInt("default_duration_ms")?.takeIf { it > 0 } ?: 280
    var speedPct by remember { mutableFloatStateOf(40f) }
    var autonomyOn by remember { mutableStateOf(false) }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            Text(
                "Nina · Drive",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Surface(shape = RoundedCornerShape(999.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                    Text(
                        if (autonomyOn) "Autonomous: ON" else "Autonomous: OFF",
                        Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
                Surface(shape = RoundedCornerShape(999.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                    Text(
                        if (bridgeOn) "BLDC (bridge)" else "BLDC not connected",
                        Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            }
        }

        if (!bridgeOn) {
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)) {
                Text(
                    "Drive bridge is off on the Jetson. Set NINA_LINK_ENABLE_ROBOT_BRIDGE=1 " +
                        "for the link daemon, restart the service, and avoid running the desktop Drive screen at the same time.",
                    Modifier.padding(12.dp),
                    color = MaterialTheme.colorScheme.onErrorContainer,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }

        actionErr?.let {
            Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
        }

        Row(
            Modifier
                .fillMaxWidth()
                .height(420.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            CameraCard(modifier = Modifier.weight(0.55f).fillMaxHeight())
            ControlCard(
                modifier = Modifier.weight(0.45f).fillMaxHeight(),
                bridgeOn = bridgeOn,
                defaultMs = defaultMs,
                speedPct = speedPct,
                onSpeedChange = { speedPct = it },
                autonomyOn = autonomyOn,
                onAutonomyChange = {
                    NinaLog.tap("Drive", "autonomy_toggle", if (it) "on" else "off")
                    autonomyOn = it
                },
                onDrive = { dir ->
                    scope.launch {
                        try {
                            vm.robotDriveMomentary(dir, defaultMs)
                            actionErr = null
                        } catch (e: Exception) {
                            actionErr = e.message
                        }
                    }
                },
                onEstop = {
                    scope.launch {
                        try {
                            vm.robotEmergencyStop()
                            actionErr = null
                        } catch (e: Exception) {
                            actionErr = e.message
                        }
                    }
                },
            )
        }

        Text(
            "Pulse ≈ ${defaultMs} ms each tap. Speed slider is visual until the daemon accepts percent.",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun CameraCard(modifier: Modifier = Modifier) {
    Card(modifier, colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("Front camera", fontWeight = FontWeight.Bold)
                Surface(shape = RoundedCornerShape(999.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                    Text(
                        "Preview — camera not connected",
                        Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            }
            Box(
                Modifier
                    .fillMaxWidth()
                    .height(240.dp),
                contentAlignment = Alignment.Center,
            ) {
                Surface(
                    Modifier.fillMaxSize(),
                    shape = RoundedCornerShape(8.dp),
                    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f),
                ) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text("Camera viewport", color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("FPS", "Latency", "Exposure", "Gain").forEach { label ->
                    Surface(
                        Modifier.weight(1f),
                        shape = RoundedCornerShape(6.dp),
                        color = MaterialTheme.colorScheme.surfaceVariant,
                    ) {
                        Column(Modifier.padding(6.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                            Text(label, style = MaterialTheme.typography.labelSmall)
                            Text("—", style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ControlCard(
    modifier: Modifier = Modifier,
    bridgeOn: Boolean,
    defaultMs: Int,
    speedPct: Float,
    onSpeedChange: (Float) -> Unit,
    autonomyOn: Boolean,
    onAutonomyChange: (Boolean) -> Unit,
    onDrive: (String) -> Unit,
    onEstop: () -> Unit,
) {
    Card(modifier, colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Manual control", fontWeight = FontWeight.Bold)
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Text("Autonomy", style = MaterialTheme.typography.bodyMedium)
                Switch(checked = autonomyOn, onCheckedChange = onAutonomyChange, enabled = bridgeOn)
            }
            Text("Speed", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Slider(value = speedPct, onValueChange = onSpeedChange, valueRange = 0f..100f, enabled = bridgeOn)
            Text("${speedPct.toInt()}%", style = MaterialTheme.typography.labelSmall)

            Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth()) {
                DrivePadButton("Forward", bridgeOn) { onDrive("forward") }
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    DrivePadButton("Left", bridgeOn) { onDrive("left") }
                    DrivePadButton("Stop", bridgeOn, PadEmphasis.Stop) { onDrive("stop") }
                    DrivePadButton("Right", bridgeOn) { onDrive("right") }
                }
                DrivePadButton("Back", bridgeOn) { onDrive("back") }
            }

            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(
                    onClick = onEstop,
                    enabled = bridgeOn,
                    modifier = Modifier.weight(1f),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
                ) {
                    Icon(Icons.Default.Stop, null, Modifier.size(18.dp))
                    Spacer(Modifier.size(4.dp))
                    Text("E-stop")
                }
            }
        }
    }
}

private enum class PadEmphasis { Normal, Stop }

@Composable
private fun DrivePadButton(
    label: String,
    enabled: Boolean,
    emphasis: PadEmphasis = PadEmphasis.Normal,
    onClick: () -> Unit,
) {
    val colors =
        when (emphasis) {
            PadEmphasis.Normal -> ButtonDefaults.buttonColors()
            PadEmphasis.Stop ->
                ButtonDefaults.buttonColors(
                    containerColor = MaterialTheme.colorScheme.secondaryContainer,
                    contentColor = MaterialTheme.colorScheme.onSecondaryContainer,
                )
        }
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = Modifier.size(width = 108.dp, height = 48.dp),
        colors = colors,
    ) {
        Text(label, style = MaterialTheme.typography.labelLarge)
    }
}
