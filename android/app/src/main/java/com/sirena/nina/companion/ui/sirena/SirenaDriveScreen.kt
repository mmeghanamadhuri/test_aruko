package com.sirena.nina.companion.ui.sirena

import android.annotation.SuppressLint
import android.webkit.WebView
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.outlined.TwoWheeler
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Surface
import com.sirena.nina.companion.ui.theme.SirenaSwitch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.util.NinaLog
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * Mirrors [sirena_ui.screens.drive_screen.DriveScreen] —
 * camera preview (MJPEG when vision bridge on), manual BLDC pulses, autonomy UI toggle.
 */
@Composable
fun SirenaDriveScreen(
    vm: CompanionViewModel,
    caps: JSONObject?,
    daemonUrl: String?,
) {
    val scope = rememberCoroutineScope()
    var actionErr by remember { mutableStateOf<String?>(null) }
    val bridgeOn = caps?.optBoolean("robot_bridge_enabled") == true
    val visionOn = caps?.optBoolean("vision_bridge_enabled") == true
    val autonomyApi = caps?.optBoolean("autonomy_bridge_enabled") == true
    val defaultMs = caps?.optInt("default_duration_ms")?.takeIf { it > 0 } ?: 280
    val speedMin = caps?.optInt("drive_speed_min_percent")?.takeIf { it in 1..99 } ?: 15
    val speedMax = caps?.optInt("drive_speed_max_percent")?.takeIf { it > speedMin } ?: 25
    var speedPct by remember(speedMin, speedMax) {
        mutableFloatStateOf(speedMin.toFloat())
    }
    var autonomyOn by remember { mutableStateOf(false) }
    var cameraPreviewOn by remember { mutableStateOf(false) }
    var bldcConnected by remember { mutableStateOf<Boolean?>(null) }
    var bldcDetail by remember { mutableStateOf<String?>(null) }
    var brakeOn by remember { mutableStateOf(true) }
    var reverseOn by remember { mutableStateOf(false) }

    val jetsonLink by vm.jetsonLink.collectAsStateWithLifecycle()
    val jetsonOnline = jetsonLink.isOnline

    val streamRoot = daemonUrl?.trimEnd('/') ?: ""

    LaunchedEffect(cameraPreviewOn, visionOn, streamRoot) {
        if (!cameraPreviewOn || !visionOn || streamRoot.isBlank()) {
            return@LaunchedEffect
        }
        val err = vm.visionOpen()
        if (err != null) actionErr = err
    }

    LaunchedEffect(cameraPreviewOn, visionOn) {
        if (cameraPreviewOn || !visionOn) return@LaunchedEffect
        vm.visionStop()
    }

    LaunchedEffect(bridgeOn, jetsonOnline) {
        if (!bridgeOn || !jetsonOnline) {
            bldcConnected = null
            bldcDetail = null
            return@LaunchedEffect
        }
        while (true) {
            val j = vm.fetchRobotDriveStatus()
            if (j != null) {
                bldcConnected = j.optBoolean("connected")
                bldcDetail = j.optString("message").takeIf { it.isNotBlank() }
            }
            delay(2500)
        }
    }

    LaunchedEffect(autonomyApi, jetsonOnline) {
        if (!autonomyApi || !jetsonOnline) return@LaunchedEffect
        while (true) {
            val st = vm.fetchAutonomyStatus()
            if (st?.optBoolean("bridge_enabled") == true) {
                autonomyOn = st.optBoolean("enabled")
            }
            delay(2000)
        }
    }

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
                        when {
                            !bridgeOn -> "Drive bridge off"
                            !jetsonOnline -> "Jetson offline — BLDC unknown"
                            bldcConnected == true -> "BLDC L+R connected"
                            bldcConnected == false -> {
                                val d = bldcDetail
                                if (!d.isNullOrBlank()) {
                                    "BLDC not connected · ${d.take(48)}"
                                } else {
                                    "BLDC not connected"
                                }
                            }

                            else -> "BLDC …"
                        },
                        Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            }
        }

        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Camera preview", style = MaterialTheme.typography.bodyMedium)
            SirenaSwitch(
                checked = cameraPreviewOn,
                onCheckedChange = { cameraPreviewOn = it },
                enabled = visionOn && streamRoot.isNotBlank(),
            )
        }
        if (!visionOn || streamRoot.isBlank()) {
            Text(
                "Enable vision bridge on the Jetson and set a daemon URL for MJPEG preview.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text("Autonomous", style = MaterialTheme.typography.bodyMedium)
                Text(
                    when {
                        !autonomyApi ->
                            "Set NINA_LINK_ENABLE_AUTONOMY_BRIDGE=1 on the Jetson (same stack as Sirena UI Map)."
                        autonomyOn ->
                            "Autonomy on — HTTP momentary drive may be refused until you turn this off."
                        else ->
                            "Wander / goto on Jetson via POST /v1/autonomy/enabled (mirrors desktop)."
                    },
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            SirenaSwitch(
                checked = autonomyOn,
                onCheckedChange = { want ->
                    if (!autonomyApi) return@SirenaSwitch
                    scope.launch {
                        val r = vm.postAutonomyEnabled(want)
                        if (r?.optBoolean("ok") == true) {
                            autonomyOn = r.optBoolean("enabled", want)
                            actionErr = null
                        } else {
                            actionErr =
                                r?.optString("error").orEmpty().ifBlank {
                                    r?.optString("message").orEmpty().ifBlank { "autonomy request failed" }
                                }
                        }
                    }
                },
                enabled = autonomyApi,
            )
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
                .heightIn(min = 280.dp, max = 520.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            CameraCard(
                modifier =
                    Modifier
                        .weight(0.55f)
                        .fillMaxHeight(),
                cameraPreviewOn = cameraPreviewOn && visionOn && streamRoot.isNotBlank(),
                streamRoot = streamRoot,
            )
            ControlCard(
                modifier =
                    Modifier
                        .weight(0.45f)
                        .fillMaxHeight(),
                bridgeOn = bridgeOn,
                brakeOn = brakeOn,
                reverseOn = reverseOn,
                onBrakeChange = { brakeOn = it },
                onReverseChange = { reverseOn = it },
                defaultMs = defaultMs,
                speedMin = speedMin,
                speedMax = speedMax,
                speedPct = speedPct,
                onSpeedChange = { speedPct = it },
                onDrive = { dir ->
                    scope.launch {
                        try {
                            if (brakeOn && dir in setOf("forward", "back", "left", "right")) {
                                actionErr = "Release brake to drive."
                                return@launch
                            }
                            val effective =
                                when (dir) {
                                    "forward" -> if (reverseOn) "back" else "forward"
                                    "back" -> if (reverseOn) "forward" else "back"
                                    else -> dir
                                }
                            vm.robotDriveMomentary(effective, defaultMs, speedPct.toInt())
                            actionErr = null
                        } catch (e: Exception) {
                            actionErr = e.message
                        }
                    }
                },
                onEstop = {
                    brakeOn = true
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
            "Pulse ≈ ${defaultMs} ms per tap; speed ${speedPct.toInt()}% is sent with each pulse. " +
                "Brake blocks D‑pad (like Sirena UI); reverse swaps forward and back.",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun CameraCard(
    modifier: Modifier = Modifier,
    cameraPreviewOn: Boolean,
    streamRoot: String,
) {
    Card(modifier, colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Outlined.TwoWheeler, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
                    Text("Front camera", fontWeight = FontWeight.Bold)
                }
                Surface(shape = RoundedCornerShape(999.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                    Text(
                        if (cameraPreviewOn) "Live (MJPEG)" else "Preview off",
                        Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            }
            Box(
                Modifier
                    .fillMaxWidth()
                    .aspectRatio(16f / 9f),
                contentAlignment = Alignment.Center,
            ) {
                if (cameraPreviewOn && streamRoot.isNotBlank()) {
                    val streamUrl = "$streamRoot/v1/vision/stream"
                    val html =
                        remember(streamUrl) {
                            "<html><head><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/></head>" +
                                "<body style=\"margin:0;background:#000;\">" +
                                "<img src=\"$streamUrl\" width=\"100%\" style=\"display:block\" />" +
                                "</body></html>"
                        }
                    DriveMjpegWebView(html = html)
                } else {
                    Surface(
                        Modifier.fillMaxSize(),
                        shape = RoundedCornerShape(8.dp),
                        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f),
                    ) {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Text(
                                "Turn on Camera preview",
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
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

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun DriveMjpegWebView(html: String) {
    AndroidView(
        factory = { context ->
            WebView(context).apply {
                settings.javaScriptEnabled = false
                settings.loadWithOverviewMode = true
                settings.useWideViewPort = true
                loadDataWithBaseURL(null, html, "text/html", "UTF-8", null)
            }
        },
        update = { wv ->
            wv.loadDataWithBaseURL(null, html, "text/html", "UTF-8", null)
        },
        modifier = Modifier.fillMaxWidth().aspectRatio(16f / 9f),
    )
}

@Composable
private fun ControlCard(
    modifier: Modifier = Modifier,
    bridgeOn: Boolean,
    brakeOn: Boolean,
    reverseOn: Boolean,
    onBrakeChange: (Boolean) -> Unit,
    onReverseChange: (Boolean) -> Unit,
    defaultMs: Int,
    speedMin: Int,
    speedMax: Int,
    speedPct: Float,
    onSpeedChange: (Float) -> Unit,
    onDrive: (String) -> Unit,
    onEstop: () -> Unit,
) {
    val smin = speedMin.toFloat()
    val smax = speedMax.toFloat()
    val steps = (speedMax - speedMin).coerceAtLeast(0)
    val padMovesEnabled = bridgeOn && !brakeOn
    Card(modifier, colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Manual control", fontWeight = FontWeight.Bold)
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text("Brake", style = MaterialTheme.typography.bodyMedium)
                    Text(
                        if (brakeOn) "Motion blocked (release to drive)" else "Released",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                SirenaSwitch(
                    checked = brakeOn,
                    onCheckedChange = onBrakeChange,
                    enabled = bridgeOn,
                )
            }
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text("Reverse", style = MaterialTheme.typography.bodyMedium)
                    Text(
                        "Swap forward / back on D‑pad",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                SirenaSwitch(checked = reverseOn, onCheckedChange = onReverseChange, enabled = bridgeOn)
            }
            Text("Speed", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Slider(
                value = speedPct.coerceIn(smin, smax),
                onValueChange = { onSpeedChange(it.coerceIn(smin, smax)) },
                valueRange = smin..smax,
                steps = steps.coerceAtLeast(0),
                enabled = bridgeOn,
            )
            Text("${speedPct.toInt()}%", style = MaterialTheme.typography.labelSmall)

            Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth()) {
                DrivePadButton("Forward", padMovesEnabled) { onDrive("forward") }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    DrivePadButton("Left", padMovesEnabled) { onDrive("left") }
                    DrivePadButton("Stop", bridgeOn, PadEmphasis.Stop) { onDrive("stop") }
                    DrivePadButton("Right", padMovesEnabled) { onDrive("right") }
                }
                DrivePadButton("Back", padMovesEnabled) { onDrive("back") }
            }

            OutlinedButton(
                onClick = onEstop,
                enabled = bridgeOn,
                modifier =
                    Modifier
                        .fillMaxWidth()
                        .heightIn(min = 48.dp),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
            ) {
                Icon(Icons.Default.Stop, null, Modifier.size(20.dp))
                Spacer(Modifier.size(8.dp))
                Text("E-stop")
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
        modifier =
            Modifier.size(width = 112.dp, height = 52.dp),
        colors = colors,
    ) {
        Text(label, style = MaterialTheme.typography.labelLarge)
    }
}
