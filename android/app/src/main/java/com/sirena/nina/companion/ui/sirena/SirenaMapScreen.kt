package com.sirena.nina.companion.ui.sirena

import android.graphics.Bitmap
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
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
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.data.SlamOccupancyGrid
import com.sirena.nina.companion.ui.theme.SirenaSwitch
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay
import org.json.JSONObject

@Composable
fun SirenaMapScreen(
    vm: CompanionViewModel,
    daemonUrl: String?,
    caps: JSONObject?,
    modifier: Modifier = Modifier,
) {
    val slamOn = caps?.optBoolean("slam_bridge_enabled") == true
    val autonomyApi = caps?.optBoolean("autonomy_bridge_enabled") == true
    val gotoApi = caps?.optBoolean("autonomy_supports_goto") == true || autonomyApi
    var autonomyOn by remember { mutableStateOf(false) }
    var mappingOn by remember { mutableStateOf(false) }
    var visionStatus by remember { mutableStateOf<JSONObject?>(null) }
    var slamStatus by remember { mutableStateOf<JSONObject?>(null) }
    var snap by remember { mutableStateOf<JSONObject?>(null) }
    var occBitmap by remember { mutableStateOf<Bitmap?>(null) }
    var mapHint by remember { mutableStateOf("") }
    var autonomyMsg by remember { mutableStateOf("") }
    var gotoMsg by remember { mutableStateOf("") }
    var gotoState by remember { mutableStateOf("idle") }
    // Tap-to-set-goal: armed via the Goto button below the map.
    var gotoArmed by remember { mutableStateOf(false) }
    // Click-position in widget pixels so we can render the pin overlay
    // without needing a pose-aware translation step.
    var goalWidget by remember { mutableStateOf<Offset?>(null) }
    var goalMm by remember { mutableStateOf<Pair<Double, Double>?>(null) }
    var snappedMm by remember { mutableStateOf<Pair<Double, Double>?>(null) }
    var pathMm by remember { mutableStateOf<List<Pair<Double, Double>>>(emptyList()) }
    var navMode by remember { mutableStateOf("—") }
    var pilotSummary by remember { mutableStateOf("") }
    var lidarHl by remember { mutableStateOf("") }
    var depthHl by remember { mutableStateOf("") }
    var irHl by remember { mutableStateOf("") }
    var ultraHl by remember { mutableStateOf("") }
    var saveMapMsg by remember { mutableStateOf("") }
    val visionEnabled = caps?.optBoolean("vision_bridge_enabled") == true
    val scope = rememberCoroutineScope()

    LaunchedEffect(daemonUrl, visionEnabled) {
        if (daemonUrl.isNullOrBlank() || !visionEnabled) return@LaunchedEffect
        while (true) {
            visionStatus = vm.fetchVisionStatus()
            delay(2000)
        }
    }

    LaunchedEffect(daemonUrl, slamOn) {
        if (daemonUrl.isNullOrBlank() || !slamOn) return@LaunchedEffect
        while (true) {
            slamStatus = vm.fetchSlamStatus()
            val s = vm.fetchSlamSnapshot()
            snap = s
            if (s != null) {
                val grid: SlamOccupancyGrid? = vm.fetchSlamOccupancyGrid()
                occBitmap = grid?.toGrayscaleBitmap()
            } else {
                occBitmap = null
            }
            mapHint = slamStatus?.optString("lidar_message").orEmpty()
            delay(600)
        }
    }

    LaunchedEffect(daemonUrl, autonomyApi) {
        if (daemonUrl.isNullOrBlank() || !autonomyApi) return@LaunchedEffect
        while (true) {
            val st = vm.fetchAutonomyStatus()
            if (st?.optBoolean("bridge_enabled") == true) {
                autonomyOn = st.optBoolean("enabled")
                navMode = st.optString("mode").ifBlank { "—" }
                st.optJSONObject("health")?.let { h ->
                    lidarHl = healthOneLine("Lidar", h.optJSONObject("lidar"))
                    depthHl = healthOneLine("Depth", h.optJSONObject("depth"))
                    irHl = healthOneLine("IR", h.optJSONObject("ir"))
                    val arr = h.optJSONArray("ultrasonic")
                    if (arr != null && arr.length() > 0) {
                        var ok = 0
                        for (i in 0 until arr.length()) {
                            val u = arr.optJSONObject(i) ?: continue
                            if (u.optBoolean("connected")) ok++
                        }
                        ultraHl = "Ultra $ok/${arr.length()}"
                    } else {
                        ultraHl = "Ultra —"
                    }
                }
                val p = st.optJSONObject("pilot")
                if (p != null) {
                    val act = p.optString("last_action")
                    val rea = p.optString("last_reason")
                    pilotSummary = listOf(act, rea).filter { it.isNotBlank() }.joinToString(" · ")
                } else {
                    pilotSummary = ""
                }
                val goto = st.optJSONObject("goto")
                if (goto != null) {
                    gotoState = goto.optString("state", gotoState)
                    val g = goto.optJSONObject("goal_mm")
                    if (g != null) {
                        goalMm = g.optDouble("x") to g.optDouble("y")
                    }
                    val sg = goto.optJSONObject("snapped_goal_mm")
                    snappedMm = sg?.let { it.optDouble("x") to it.optDouble("y") }
                    val arr = goto.optJSONArray("waypoints_mm")
                    if (arr != null) {
                        val pts = mutableListOf<Pair<Double, Double>>()
                        for (i in 0 until arr.length()) {
                            val w = arr.optJSONObject(i) ?: continue
                            pts.add(w.optDouble("x") to w.optDouble("y"))
                        }
                        pathMm = pts
                    }
                    val reason = goto.optString("reason")
                    if (reason.isNotBlank()) gotoMsg = "$gotoState · $reason"
                    // Clear overlays on a clean arrival/cancel to keep the
                    // map readable for the next click.
                    if (gotoState == "arrived" || gotoState == "cancelled") {
                        pathMm = emptyList()
                    }
                }
            }
            delay(1500)
        }
    }

    Column(
        modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(
                "Nina · Map",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Surface(shape = RoundedCornerShape(999.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                    Text(
                        if (slamOn) "SLAM: ${slamStatus?.optString("lidar_message")?.take(16) ?: "…"}"
                        else "SLAM: off (daemon)",
                        Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
                Surface(shape = RoundedCornerShape(999.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                    Text(
                        if (autonomyApi) {
                            if (autonomyOn) "Autonomy: ON" else "Autonomy: OFF"
                        } else {
                            "Autonomy: n/a"
                        },
                        Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
                Surface(shape = RoundedCornerShape(999.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                    Text(
                        "Mode: $navMode",
                        Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
                Surface(shape = RoundedCornerShape(999.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                    Text(
                        "Goto: $gotoState",
                        Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            }
        }

        if (slamOn) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                MapLegendDot(Color(0xFFC8102E), "Nina")
                MapLegendDot(Color(0xFF1C1C1E), "Wall")
                MapLegendDot(Color(0xFFD1D1D6), "Free")
                MapLegendDot(Color(0xFF8E8E93), "Unknown")
            }
        }

        Row(
            Modifier
                .fillMaxWidth()
                .height(320.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Card(
                Modifier
                    .weight(0.62f)
                    .fillMaxSize(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f)),
            ) {
                // Track the rendered bitmap rect so taps can be
                // converted to world mm using the snapshot's scale.
                var imageSize by remember { mutableStateOf(IntSize.Zero) }
                var imageOffset by remember { mutableStateOf(IntOffset.Zero) }
                Box(
                    Modifier
                        .fillMaxSize()
                        .pointerInput(gotoArmed, snap, occBitmap) {
                            if (!gotoArmed || snap == null || occBitmap == null) return@pointerInput
                            detectTapGestures { tap ->
                                val sn = snap ?: return@detectTapGestures
                                val bmp = occBitmap ?: return@detectTapGestures
                                val box = imageSize
                                if (box.width <= 0 || box.height <= 0) return@detectTapGestures
                                // Tap is in widget coords relative to the
                                // image; clamp to inside the bitmap.
                                val tx = (tap.x - imageOffset.x).coerceIn(0f, box.width.toFloat())
                                val ty = (tap.y - imageOffset.y).coerceIn(0f, box.height.toFloat())
                                val pxX = tx / box.width * bmp.width
                                val pxY = ty / box.height * bmp.height
                                val scale = sn.optDouble("scale_mm_per_px", 1.0)
                                val w = sn.optInt("width", bmp.width)
                                val h = sn.optInt("height", bmp.height)
                                val cx = w / 2.0
                                val cy = h / 2.0
                                val xMm = (pxX - cx) * scale
                                val yMm = (cy - pxY) * scale
                                goalWidget = Offset(tap.x, tap.y)
                                goalMm = xMm to yMm
                                pathMm = emptyList()
                                snappedMm = null
                                scope.launch {
                                    val r = vm.postAutonomyGoal(xMm, yMm)
                                    if (r?.optBoolean("ok") == true) {
                                        autonomyOn = true
                                        gotoState = r.optString("mode", "goto")
                                        gotoMsg = r.optString("message").orEmpty()
                                    } else {
                                        gotoMsg = r?.optString("message").orEmpty()
                                            .ifBlank { "goto request failed" }
                                    }
                                }
                            }
                        },
                    contentAlignment = Alignment.Center,
                ) {
                    if (slamOn && occBitmap != null) {
                        Image(
                            bitmap = occBitmap!!.asImageBitmap(),
                            contentDescription = "SLAM occupancy",
                            modifier = Modifier
                                .fillMaxSize()
                                .padding(4.dp),
                        )
                        // Overlay path polyline + goal flag on top of
                        // the bitmap. The overlay uses the same Box so
                        // the IntSize / IntOffset of the contained
                        // bitmap rect is what we measure for tap math.
                        Canvas(Modifier.fillMaxSize()) {
                            val box = size
                            imageSize = IntSize(box.width.toInt(), box.height.toInt())
                            imageOffset = IntOffset(0, 0)
                            val sn = snap
                            val bmp = occBitmap
                            if (sn != null && bmp != null && bmp.width > 0 && bmp.height > 0) {
                                val scale = sn.optDouble("scale_mm_per_px", 1.0)
                                val gw = sn.optInt("width", bmp.width)
                                val gh = sn.optInt("height", bmp.height)
                                val cx = gw / 2.0
                                val cy = gh / 2.0
                                fun mmToWidget(xMm: Double, yMm: Double): Offset {
                                    val pxX = (cx + xMm / scale)
                                    val pxY = (cy - yMm / scale)
                                    return Offset(
                                        (pxX / gw * box.width).toFloat(),
                                        (pxY / gh * box.height).toFloat(),
                                    )
                                }
                                // Path
                                if (pathMm.size >= 2) {
                                    val red = Color(0xFFC8102E).copy(alpha = 0.85f)
                                    val effect = PathEffect.dashPathEffect(floatArrayOf(10f, 8f), 0f)
                                    for (i in 0 until pathMm.size - 1) {
                                        drawLine(
                                            color = red,
                                            start = mmToWidget(pathMm[i].first, pathMm[i].second),
                                            end = mmToWidget(pathMm[i + 1].first, pathMm[i + 1].second),
                                            strokeWidth = 4f,
                                            pathEffect = effect,
                                        )
                                    }
                                }
                                // Goal pin (snapped wins if present)
                                val pin = snappedMm ?: goalMm
                                if (pin != null) {
                                    val center = mmToWidget(pin.first, pin.second)
                                    drawCircle(
                                        color = Color(0xFFC8102E),
                                        radius = 12f,
                                        center = center,
                                    )
                                    drawCircle(
                                        color = Color.White,
                                        radius = 12f,
                                        center = center,
                                        style = Stroke(width = 3f),
                                    )
                                }
                                if (snappedMm != null && goalMm != null) {
                                    val raw = mmToWidget(goalMm!!.first, goalMm!!.second)
                                    drawCircle(
                                        color = Color(0xFFC8102E).copy(alpha = 0.6f),
                                        radius = 10f,
                                        center = raw,
                                        style = Stroke(width = 3f),
                                    )
                                }
                            }
                        }
                    } else {
                        Text(
                            if (!slamOn) "Enable NINA_LINK_ENABLE_SLAM_BRIDGE on the Jetson for the map."
                            else if (occBitmap == null) "Waiting for SLAM grid…"
                            else "",
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
            Column(
                Modifier
                    .weight(0.38f)
                    .fillMaxSize(),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("Pose", fontWeight = FontWeight.Bold)
                        val pose = snap?.optJSONObject("pose")
                        if (pose != null) {
                            val x = pose.optLong("x_mm")
                            val y = pose.optLong("y_mm")
                            val th = pose.optDouble("theta_deg")
                            Text(
                                "x ${x} mm  ·  y ${y} mm  ·  θ ${"%.1f".format(th)}°",
                                style = MaterialTheme.typography.bodySmall,
                            )
                        } else {
                            Text("x —  y —  θ —", style = MaterialTheme.typography.bodySmall)
                        }
                        val detail =
                            listOfNotNull(
                                mapHint.ifBlank { null },
                                visionStatus?.optString("message")?.ifBlank { null },
                            ).joinToString(" · ")
                        Text(
                            detail.ifBlank { "Pose from /v1/slam/snapshot when the bridge is on." },
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                if (autonomyApi) {
                    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text("Sensor health", fontWeight = FontWeight.Bold)
                            Text(
                                listOf(lidarHl, depthHl, irHl, ultraHl).joinToString("  ·  "),
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text("Pilot (wander)", fontWeight = FontWeight.Bold)
                            Text(
                                pilotSummary.ifBlank { "idle" },
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                    }
                }
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
                    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Text("Controls", fontWeight = FontWeight.Bold)
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                            Text("Autonomous", style = MaterialTheme.typography.bodySmall)
                            SirenaSwitch(
                                checked = autonomyOn,
                                onCheckedChange = { want ->
                                    if (!autonomyApi) return@SirenaSwitch
                                    scope.launch {
                                        val r = vm.postAutonomyEnabled(want)
                                        if (r?.optBoolean("ok") == true) {
                                            autonomyOn = r.optBoolean("enabled", want)
                                            autonomyMsg = r.optString("message").orEmpty()
                                        } else {
                                            autonomyMsg =
                                                r?.optString("error").orEmpty().ifBlank {
                                                    r?.optString("message").orEmpty().ifBlank { "autonomy request failed" }
                                                }
                                        }
                                    }
                                },
                                enabled = autonomyApi,
                            )
                        }
                        if (autonomyMsg.isNotBlank()) {
                            Text(autonomyMsg, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.error)
                        }
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Button(
                                onClick = {
                                    gotoArmed = !gotoArmed
                                    if (!gotoArmed) {
                                        goalWidget = null
                                        goalMm = null
                                        pathMm = emptyList()
                                        snappedMm = null
                                    }
                                },
                                enabled = gotoApi && slamOn,
                                modifier = Modifier.weight(1f),
                            ) { Text(if (gotoArmed) "Tap: ARMED" else "Go to point") }
                            OutlinedButton(
                                onClick = {
                                    scope.launch {
                                        val r = vm.deleteAutonomyGoal()
                                        gotoMsg = r?.optString("message").orEmpty()
                                            .ifBlank { "goto cleared" }
                                        gotoArmed = false
                                        goalWidget = null
                                        goalMm = null
                                        pathMm = emptyList()
                                        snappedMm = null
                                    }
                                },
                                enabled = gotoApi,
                                modifier = Modifier.weight(1f),
                            ) { Text("Cancel goto") }
                        }
                        if (gotoMsg.isNotBlank()) {
                            Text(
                                gotoMsg,
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Button(
                                onClick = { mappingOn = true },
                                enabled = slamOn,
                                modifier = Modifier.weight(1f),
                            ) { Text("Start mapping") }
                            OutlinedButton(
                                onClick = { mappingOn = false },
                                enabled = slamOn,
                                modifier = Modifier.weight(1f),
                            ) { Text("Stop mapping") }
                        }
                        Text(
                            "Mapping runs with the SLAM bridge; use Save on-robot if you add a file endpoint later.",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            OutlinedButton(
                                onClick = {
                                    scope.launch {
                                        saveMapMsg = ""
                                        val r = vm.saveSlamMapPgm("nina_map.pgm")
                                        saveMapMsg =
                                            when {
                                                r == null -> "Save failed (unreachable host)."
                                                r.optBoolean("ok") ->
                                                    "Saved: ${r.optString("filename", "nina_map.pgm")}"
                                                else ->
                                                    r.optString("detail", r.optString("message", "Save failed"))
                                            }
                                    }
                                },
                                enabled = slamOn,
                                modifier = Modifier.weight(1f),
                            ) {
                                Text("Save map")
                            }
                            OutlinedButton(onClick = {}, enabled = false, modifier = Modifier.weight(1f)) { Text("Clear") }
                        }
                        if (saveMapMsg.isNotBlank()) {
                            Text(
                                saveMapMsg,
                                style = MaterialTheme.typography.labelSmall,
                                color =
                                    if (saveMapMsg.startsWith("Saved")) {
                                        MaterialTheme.colorScheme.primary
                                    } else {
                                        MaterialTheme.colorScheme.error
                                    },
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun MapLegendDot(color: Color, label: String) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Box(
            Modifier
                .size(8.dp)
                .background(color, CircleShape),
        )
        Text(
            label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

private fun healthOneLine(title: String, o: JSONObject?): String {
    if (o == null) return "$title: —"
    val ok = o.optBoolean("connected")
    val msg = o.optString("message").trim()
    return if (ok) {
        "$title: OK"
    } else {
        val short = if (msg.length > 18) msg.take(18) + "…" else msg
        "$title: ${short.ifBlank { "off" }}"
    }
}

private fun SlamOccupancyGrid.toGrayscaleBitmap(): Bitmap? {
    if (bytes.size < width * height) return null
    val pixels = IntArray(width * height)
    var i = 0
    for (idx in pixels.indices) {
        val v = bytes[i].toInt() and 0xff
        i++
        pixels[idx] = (0xff shl 24) or (v shl 16) or (v shl 8) or v
    }
    return Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888).apply {
        setPixels(pixels, 0, width, 0, 0, width, height)
    }
}
