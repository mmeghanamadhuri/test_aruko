package com.sirena.nina.companion.ui.sirena

import android.annotation.SuppressLint
import android.webkit.WebView
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
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
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.TwoWheeler
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Icon
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
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.util.NinaLog
import kotlin.math.sqrt
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

/** Non-HTTP failures use [LinkApiException]; HTTP 200 with `ok: false` surfaces here. */
private fun JSONObject.driveCommandErrorOrNull(): String? {
    if (length() == 0) return null
    if (optBoolean("ok", true)) return null
    val e = optString("error").trim()
    if (e.isNotEmpty() && !e.equals("null", ignoreCase = true)) return e
    val m = optString("message").trim()
    if (m.isNotEmpty() && !m.equals("null", ignoreCase = true)) return m
    return "Drive request failed"
}

private fun batteryLabelFromHealth(h: JSONObject?): String {
    val rows = h?.optJSONArray("rows") ?: return "n/a"
    for (i in 0 until rows.length()) {
        val o = rows.optJSONObject(i) ?: continue
        if (o.optString("key") == "battery") {
            return o.optString("detail").trim().ifBlank { "—" }
        }
    }
    return "n/a"
}

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
    var invertLeft by remember { mutableStateOf(false) }
    var invertRight by remember { mutableStateOf(false) }
    var hudHeading by remember { mutableStateOf("—") }
    var hudDistance by remember { mutableStateOf("—") }
    var batteryLabel by remember { mutableStateOf("n/a") }
    val slamOn = caps?.optBoolean("slam_bridge_enabled") == true

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
                val msg = j.optString("message").trim()
                val lde = j.optString("last_drive_error").trim()
                bldcDetail =
                    when {
                        msg.isNotEmpty() && lde.isNotEmpty() -> "$msg · $lde"
                        msg.isNotEmpty() -> msg
                        lde.isNotEmpty() -> lde
                        else -> null
                    }
                if (j.has("invert_left")) invertLeft = j.optBoolean("invert_left")
                if (j.has("invert_right")) invertRight = j.optBoolean("invert_right")
            }
            delay(2500)
        }
    }

    LaunchedEffect(jetsonOnline) {
        if (!jetsonOnline) {
            batteryLabel = "n/a"
            return@LaunchedEffect
        }
        while (true) {
            batteryLabel = batteryLabelFromHealth(vm.fetchRobotHealth())
            delay(3000)
        }
    }

    LaunchedEffect(slamOn, jetsonOnline) {
        if (!slamOn || !jetsonOnline) {
            hudHeading = "—"
            hudDistance = "—"
            return@LaunchedEffect
        }
        while (true) {
            val st = try {
                vm.fetchSlamStatus()
            } catch (_: Exception) {
                null
            }
            val snap = st?.optJSONObject("snapshot")
            val pose = snap?.optJSONObject("pose")
            if (pose != null) {
                val th = pose.optDouble("theta_deg", Double.NaN)
                hudHeading = if (th.isFinite()) String.format("%.0f°", th) else "—"
                val x = pose.optDouble("x_mm", 0.0)
                val y = pose.optDouble("y_mm", 0.0)
                val m = sqrt(x * x + y * y) / 1000.0
                hudDistance = String.format("%.1f m", m)
            } else {
                hudHeading = "—"
                hudDistance = "—"
            }
            delay(2000)
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
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(0.dp),
    ) {
        // Match desktop Sirena red title bar
        Surface(
            modifier = Modifier.fillMaxWidth(),
            color = MaterialTheme.colorScheme.primary,
            shadowElevation = 4.dp,
        ) {
            Column(Modifier.fillMaxWidth().padding(vertical = 14.dp, horizontal = 16.dp)) {
                Text(
                    "Nina · Drive",
                    modifier = Modifier.fillMaxWidth(),
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.onPrimary,
                )
            }
        }
        Column(
            Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
        Text(
            "Nina / Drive",
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            val autoColors =
                if (autonomyOn) {
                    CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.primary,
                        contentColor = MaterialTheme.colorScheme.onPrimary,
                    )
                } else {
                    CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)
                }
            Card(
                colors = autoColors,
                shape = RoundedCornerShape(999.dp),
            ) {
                Text(
                    "Autonomous: " + if (autonomyOn) "ON" else "OFF",
                    Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Medium,
                )
            }
            val (bldcBg, bldcFg) =
                when {
                    !bridgeOn || !jetsonOnline ->
                        MaterialTheme.colorScheme.surfaceVariant to MaterialTheme.colorScheme.onSurfaceVariant
                    bldcConnected == true ->
                        MaterialTheme.colorScheme.primaryContainer to MaterialTheme.colorScheme.onPrimaryContainer
                    bldcConnected == false ->
                        MaterialTheme.colorScheme.errorContainer to MaterialTheme.colorScheme.onErrorContainer
                    else ->
                        MaterialTheme.colorScheme.surfaceVariant to MaterialTheme.colorScheme.onSurfaceVariant
                }
            Card(
                colors = CardDefaults.cardColors(containerColor = bldcBg, contentColor = bldcFg),
                shape = RoundedCornerShape(999.dp),
                modifier = Modifier.weight(1f, fill = false).widthIn(max = 280.dp),
            ) {
                Text(
                    when {
                        !bridgeOn -> "BLDC · bridge off"
                        !jetsonOnline -> "BLDC · offline"
                        bldcConnected == true -> "BLDC · connected"
                        bldcConnected == false -> {
                            val d = bldcDetail
                            if (!d.isNullOrBlank()) "BLDC · ${d.take(48)}" else "BLDC · not connected"
                        }
                        else -> "BLDC · checking…"
                    },
                    Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Medium,
                )
            }
        }

        // Desktop-style telemetry bar (always visible; values update when SLAM/health available)
        Surface(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(10.dp),
            color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.85f),
        ) {
            Row(
                Modifier.padding(horizontal = 8.dp, vertical = 10.dp).fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly,
            ) {
                DriveTelemetryPill("SPEED", "${speedPct.toInt()}%")
                DriveTelemetryPill("HEADING", hudHeading)
                DriveTelemetryPill("DISTANCE", hudDistance)
                DriveTelemetryPill("BATTERY", batteryLabel.uppercase())
            }
        }

        Text(
            "Camera & preview",
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.onSurface,
        )
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Live MJPEG", style = MaterialTheme.typography.bodyMedium)
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
        if (bridgeOn && jetsonOnline && bldcConnected == false) {
            Card(
                colors =
                    CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.tertiaryContainer,
                    ),
            ) {
                Text(
                    "BLDC path failed under nina-link. Match Sirena UI: copy " +
                        "nina/systemd/nina-link-navigation.env.example to /etc/nina-link/navigation.env " +
                        "(NINA_NAV_REMOTE_PORT, NINA_NAV_MODE=remote), restart nina-link, and ensure " +
                        "the desktop Drive screen is not holding the same UART.",
                    Modifier.padding(12.dp),
                    color = MaterialTheme.colorScheme.onTertiaryContainer,
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }

        actionErr?.let { err ->
            Card(
                colors =
                    CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.errorContainer,
                    ),
                shape = RoundedCornerShape(10.dp),
            ) {
                Row(
                    Modifier.padding(12.dp).fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        "\u26A0",
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onErrorContainer,
                    )
                    Text(
                        err,
                        modifier = Modifier.weight(1f),
                        color = MaterialTheme.colorScheme.onErrorContainer,
                        style = MaterialTheme.typography.bodySmall,
                        fontWeight = FontWeight.Medium,
                    )
                }
            }
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
                autonomyOn = autonomyOn,
                autonomyApi = autonomyApi,
                onAutonomyChange = { want ->
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
                invertLeft = invertLeft,
                invertRight = invertRight,
                onInvertLeft = { on ->
                    scope.launch {
                        val r = vm.postRobotDriveInvert(on, null)
                        if (r?.optBoolean("ok") == true) {
                            invertLeft = r.optBoolean("invert_left", on)
                            actionErr = null
                        } else {
                            actionErr = r?.optString("error") ?: "invert failed"
                        }
                    }
                },
                onInvertRight = { on ->
                    scope.launch {
                        val r = vm.postRobotDriveInvert(null, on)
                        if (r?.optBoolean("ok") == true) {
                            invertRight = r.optBoolean("invert_right", on)
                            actionErr = null
                        } else {
                            actionErr = r?.optString("error") ?: "invert failed"
                        }
                    }
                },
                brakeOn = brakeOn,
                reverseOn = reverseOn,
                onBrakeChange = { brakeOn = it },
                onReverseChange = { reverseOn = it },
                speedMin = speedMin,
                speedMax = speedMax,
                speedPct = speedPct,
                onSpeedChange = { speedPct = it },
                pulseIntervalMs = defaultMs.toLong().coerceAtLeast(50L),
                onDrivePulse = { dir ->
                    if (brakeOn && dir in setOf("forward", "back", "left", "right")) {
                        JSONObject().put("ok", false).put("error", "Release brake to drive.")
                    } else {
                        val effective =
                            when (dir) {
                                "forward" -> if (reverseOn) "back" else "forward"
                                "back" -> if (reverseOn) "forward" else "back"
                                else -> dir
                            }
                        vm.robotDriveMomentary(effective, defaultMs, speedPct.toInt())
                    }
                },
                onDriveResult = { j -> actionErr = j.driveCommandErrorOrNull() },
                onEstop = {
                    brakeOn = true
                    scope.launch {
                        try {
                            val j = vm.robotEmergencyStop()
                            actionErr = j.driveCommandErrorOrNull()
                        } catch (e: Exception) {
                            actionErr = e.message
                        }
                    }
                },
            )
        }

        Surface(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(8.dp),
            color = MaterialTheme.colorScheme.secondaryContainer.copy(alpha = 0.5f),
        ) {
            Text(
                "Hold D‑pad: ~${defaultMs} ms pulses · brake / reverse / flip L·R match desktop · E‑STOP stops motion.",
                Modifier.padding(10.dp),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSecondaryContainer,
            )
        }
        }
    }
}

@Composable
private fun DriveTelemetryPill(
    title: String,
    value: String,
) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier.padding(horizontal = 4.dp),
    ) {
        Text(
            title,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            fontWeight = FontWeight.Medium,
        )
        Text(
            value,
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.onSurface,
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
    Card(
        modifier,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
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
                                "USB camera not connected",
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                style = MaterialTheme.typography.bodySmall,
                            )
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
    autonomyOn: Boolean,
    autonomyApi: Boolean,
    onAutonomyChange: (Boolean) -> Unit,
    invertLeft: Boolean,
    invertRight: Boolean,
    onInvertLeft: (Boolean) -> Unit,
    onInvertRight: (Boolean) -> Unit,
    brakeOn: Boolean,
    reverseOn: Boolean,
    onBrakeChange: (Boolean) -> Unit,
    onReverseChange: (Boolean) -> Unit,
    speedMin: Int,
    speedMax: Int,
    speedPct: Float,
    onSpeedChange: (Float) -> Unit,
    pulseIntervalMs: Long,
    onDrivePulse: suspend (String) -> JSONObject,
    onDriveResult: (JSONObject) -> Unit,
    onEstop: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val smin = speedMin.toFloat()
    val smax = speedMax.toFloat()
    val steps = (speedMax - speedMin).coerceAtLeast(0)
    val padMovesEnabled = bridgeOn && !brakeOn
    Card(
        modifier,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
    ) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(
                "Manual driving",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.primary,
            )
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "Autonomous nav",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(
                        if (autonomyApi) {
                            if (autonomyOn) "ON" else "OFF"
                        } else {
                            "N/A"
                        },
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.Bold,
                        color =
                            if (autonomyOn) MaterialTheme.colorScheme.primary
                            else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    SirenaSwitch(
                        checked = autonomyOn,
                        onCheckedChange = onAutonomyChange,
                        enabled = autonomyApi,
                    )
                }
            }

            Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.fillMaxWidth()) {
                DrivePadRepeatButton("Forward", padMovesEnabled, pulseIntervalMs, {
                    val j = onDrivePulse("forward")
                    onDriveResult(j)
                })
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    DrivePadRepeatButton("Left", padMovesEnabled, pulseIntervalMs, {
                        val j = onDrivePulse("left")
                        onDriveResult(j)
                    })
                    DrivePadButton("Stop", bridgeOn, PadEmphasis.Stop) {
                        scope.launch {
                            val j = onDrivePulse("stop")
                            onDriveResult(j)
                        }
                    }
                    DrivePadRepeatButton("Right", padMovesEnabled, pulseIntervalMs, {
                        val j = onDrivePulse("right")
                        onDriveResult(j)
                    })
                }
                DrivePadRepeatButton("Back", padMovesEnabled, pulseIntervalMs, {
                    val j = onDrivePulse("back")
                    onDriveResult(j)
                })
            }

            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Text("Speed", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Slider(
                    modifier = Modifier.weight(1f),
                    value = speedPct.coerceIn(smin, smax),
                    onValueChange = { onSpeedChange(it.coerceIn(smin, smax)) },
                    valueRange = smin..smax,
                    steps = steps.coerceAtLeast(0),
                    enabled = bridgeOn,
                )
                Surface(shape = RoundedCornerShape(999.dp), color = MaterialTheme.colorScheme.errorContainer) {
                    Text(
                        "${speedPct.toInt()}%",
                        Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onErrorContainer,
                    )
                }
            }

            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "Wheels",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("Flip L", style = MaterialTheme.typography.labelSmall)
                    SirenaSwitch(
                        checked = invertLeft,
                        onCheckedChange = onInvertLeft,
                        enabled = bridgeOn,
                    )
                    Text("Flip R", style = MaterialTheme.typography.labelSmall)
                    SirenaSwitch(
                        checked = invertRight,
                        onCheckedChange = onInvertRight,
                        enabled = bridgeOn,
                    )
                }
            }

            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                OutlinedButton(
                    onClick = { onBrakeChange(!brakeOn) },
                    enabled = bridgeOn,
                    modifier = Modifier.height(40.dp),
                ) {
                    Text(if (brakeOn) "Brake: ON" else "Brake: OFF")
                }
                OutlinedButton(
                    onClick = { onReverseChange(!reverseOn) },
                    enabled = bridgeOn,
                    modifier = Modifier.height(40.dp),
                ) {
                    Text(if (reverseOn) "Reverse: ON" else "Reverse: OFF")
                }
                Spacer(Modifier.weight(1f))
                Button(
                    onClick = onEstop,
                    enabled = bridgeOn,
                    modifier = Modifier.height(40.dp),
                    colors =
                        ButtonDefaults.buttonColors(
                            containerColor = MaterialTheme.colorScheme.error,
                            contentColor = MaterialTheme.colorScheme.onError,
                        ),
                ) {
                    Text("\u26A0 E‑STOP")
                }
            }

            Text(
                "Tip: hold D‑pad to repeat; Stop sends one command. Desktop: WASD · Space · Esc.",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

private enum class PadEmphasis { Normal, Stop }

@Composable
private fun DrivePadRepeatButton(
    label: String,
    enabled: Boolean,
    repeatIntervalMs: Long,
    onPulse: suspend () -> Unit,
) {
    val interactionSource = remember { MutableInteractionSource() }
    val pressed by interactionSource.collectIsPressedAsState()
    val latestPulse by rememberUpdatedState(onPulse)
    LaunchedEffect(pressed, enabled, repeatIntervalMs) {
        if (!pressed || !enabled) return@LaunchedEffect
        while (true) {
            latestPulse()
            delay(repeatIntervalMs)
        }
    }
    Button(
        onClick = {},
        enabled = enabled,
        interactionSource = interactionSource,
        modifier = Modifier.size(width = 112.dp, height = 52.dp),
        colors = ButtonDefaults.buttonColors(),
    ) {
        Text(label, style = MaterialTheme.typography.labelLarge)
    }
}

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
                    containerColor = MaterialTheme.colorScheme.primary,
                    contentColor = MaterialTheme.colorScheme.onPrimary,
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
