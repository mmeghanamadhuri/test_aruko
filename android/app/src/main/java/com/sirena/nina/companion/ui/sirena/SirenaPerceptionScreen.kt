package com.sirena.nina.companion.ui.sirena

import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.webkit.WebView
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
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
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.data.SlamOccupancyGrid
import com.sirena.nina.companion.ui.theme.SirenaSwitch
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

@Composable
fun SirenaPerceptionScreen(
    vm: CompanionViewModel,
    daemonUrl: String?,
    caps: JSONObject?,
    modifier: Modifier = Modifier,
) {
    val streamRoot = daemonUrl?.trimEnd('/') ?: ""
    val visionOn = caps?.optBoolean("vision_bridge_enabled") == true
    val slamOn = caps?.optBoolean("slam_bridge_enabled") == true
    val depthOn = caps?.optBoolean("depth_bridge_enabled") == true
    val autonomyApi = caps?.optBoolean("autonomy_bridge_enabled") == true

    var cameraOn by remember { mutableStateOf(false) }
    var autonomyOn by remember { mutableStateOf(false) }
    var visionStatus by remember { mutableStateOf<JSONObject?>(null) }
    var statusLine by remember { mutableStateOf("") }
    var lidarBitmap by remember { mutableStateOf<Bitmap?>(null) }
    var autonomyHint by remember { mutableStateOf("") }
    var autoPill by remember { mutableStateOf("Autonomous: …") }
    var lidarPill by remember { mutableStateOf("Lidar: …") }
    var depthPill by remember { mutableStateOf("Depth: …") }
    val scope = rememberCoroutineScope()

    LaunchedEffect(cameraOn, visionOn) {
        if (!visionOn) return@LaunchedEffect
        if (cameraOn) {
            vm.visionOpen()
        } else {
            vm.visionStop()
        }
    }

    LaunchedEffect(visionOn, cameraOn) {
        if (!visionOn || !cameraOn) return@LaunchedEffect
        while (true) {
            visionStatus = vm.fetchVisionStatus()
            statusLine = visionStatus?.optString("message").orEmpty()
            delay(1500)
        }
    }

    LaunchedEffect(slamOn, streamRoot) {
        if (!slamOn || streamRoot.isBlank()) {
            lidarBitmap = null
            return@LaunchedEffect
        }
        while (true) {
            if (vm.fetchSlamSnapshot() != null) {
                val g = vm.fetchSlamOccupancyGrid()
                lidarBitmap = g?.toOccBitmap()
            } else {
                lidarBitmap = null
            }
            delay(800)
        }
    }

    LaunchedEffect(autonomyApi, streamRoot) {
        if (!autonomyApi || streamRoot.isBlank()) {
            autoPill = "Autonomous: n/a"
            return@LaunchedEffect
        }
        while (true) {
            val st = vm.fetchAutonomyStatus()
            if (st?.optBoolean("bridge_enabled") == true) {
                autonomyOn = st.optBoolean("enabled")
                autoPill = if (autonomyOn) "Autonomous: ON" else "Autonomous: OFF"
                st.optJSONObject("health")?.let { h ->
                    lidarPill = perceptionHealthLine("Lidar", h.optJSONObject("lidar"), slamOn)
                    depthPill = perceptionHealthLine("Depth", h.optJSONObject("depth"), depthOn)
                }
            }
            delay(2000)
        }
    }

    LaunchedEffect(depthOn, streamRoot) {
        if (!depthOn || streamRoot.isBlank()) return@LaunchedEffect
        while (true) {
            val ds = vm.fetchDepthStatus()
            if (ds?.optBoolean("camera_open") == true) {
                depthPill = "Depth: stream"
            } else if (depthOn) {
                val m = ds?.optString("message").orEmpty().ifBlank { "off" }
                depthPill = "Depth: ${m.take(20)}"
            }
            delay(3000)
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
                "Nina · Perception",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                Pill(autoPill)
                Pill(
                    if (slamOn) {
                        if (lidarPill != "Lidar: …") lidarPill else if (lidarBitmap != null) "Lidar: map" else "Lidar: …"
                    } else {
                        "Lidar: bridge off"
                    },
                )
                Pill(
                    if (depthOn) depthPill else "Depth: bridge off",
                )
                Pill(
                    when {
                        !visionOn -> "Cam: bridge off"
                        cameraOn -> "Cam: live"
                        else -> "Cam: off"
                    },
                )
            }
        }

        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            Text("Autonomous", style = MaterialTheme.typography.bodyMedium)
            SirenaSwitch(
                checked = autonomyOn,
                onCheckedChange = { want ->
                    if (!autonomyApi) return@SirenaSwitch
                    scope.launch {
                        val r = vm.postAutonomyEnabled(want)
                        autonomyHint =
                            if (r?.optBoolean("ok") == true) {
                                ""
                            } else {
                                r?.optString("error").orEmpty().ifBlank {
                                    r?.optString("message").orEmpty().ifBlank { "autonomy request failed" }
                                }
                            }
                    }
                },
                enabled = autonomyApi,
            )
        }
        if (autonomyHint.isNotBlank()) {
            Text(
                autonomyHint,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.error,
            )
        }
        if (!autonomyApi) {
            Text(
                "Autonomy API off — set NINA_LINK_ENABLE_AUTONOMY_BRIDGE=1 on the Jetson.",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        Row(
            Modifier
                .fillMaxWidth()
                .height(260.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            LidarSlamPane(
                title = "LiDAR / SLAM",
                bitmap = if (slamOn) lidarBitmap else null,
                modifier = Modifier.weight(1f),
            )
            CameraPane(
                title = "RGB",
                streamRoot = streamRoot,
                streamPath = "/v1/vision/stream",
                enabled = cameraOn && visionOn && streamRoot.isNotBlank(),
                modifier = Modifier.weight(1f),
            )
            CameraPane(
                title = "Depth",
                streamRoot = streamRoot,
                streamPath = "/v1/depth/stream",
                enabled = depthOn && streamRoot.isNotBlank(),
                modifier = Modifier.weight(1f),
            )
        }

        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            Text("Camera stream", style = MaterialTheme.typography.bodyMedium)
            SirenaSwitch(
                checked = cameraOn,
                onCheckedChange = { cameraOn = it },
                enabled = visionOn && streamRoot.isNotBlank(),
            )
        }
        if (statusLine.isNotBlank()) {
            Text(
                statusLine,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun LidarSlamPane(title: String, bitmap: Bitmap?, modifier: Modifier = Modifier) {
    Card(modifier.fillMaxSize(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            Box(Modifier.fillMaxWidth().aspectRatio(16f / 9f), contentAlignment = Alignment.Center) {
                if (bitmap != null) {
                    Image(
                        bitmap = bitmap.asImageBitmap(),
                        contentDescription = "SLAM occupancy",
                        modifier = Modifier.fillMaxSize(),
                    )
                } else {
                    Surface(
                        Modifier.fillMaxSize(),
                        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f),
                        shape = MaterialTheme.shapes.medium,
                    ) {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Text(
                                "SLAM grid from /v1/slam/occupancy",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun Pill(text: String) {
    Surface(shape = MaterialTheme.shapes.small, color = MaterialTheme.colorScheme.surfaceVariant) {
        Text(text, Modifier.padding(horizontal = 8.dp, vertical = 4.dp), style = MaterialTheme.typography.labelSmall)
    }
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun CameraPane(
    title: String,
    streamRoot: String,
    streamPath: String,
    enabled: Boolean,
    modifier: Modifier = Modifier,
) {
    Card(modifier.fillMaxSize(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            Box(Modifier.fillMaxWidth().aspectRatio(16f / 9f), contentAlignment = Alignment.Center) {
                if (enabled) {
                    val streamUrl = "$streamRoot$streamPath"
                    val html =
                        remember(streamUrl) {
                            "<html><body style=\"margin:0;background:#000;\"><img src=\"$streamUrl\" width=\"100%\"/></body></html>"
                        }
                    AndroidView(
                        factory = { context ->
                            WebView(context).apply {
                                settings.javaScriptEnabled = false
                                loadDataWithBaseURL(null, html, "text/html", "UTF-8", null)
                            }
                        },
                        update = { it.loadDataWithBaseURL(null, html, "text/html", "UTF-8", null) },
                        modifier = Modifier.fillMaxSize(),
                    )
                } else {
                    Surface(
                        Modifier.fillMaxSize(),
                        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f),
                        shape = MaterialTheme.shapes.medium,
                    ) {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Text(
                                if (streamRoot.isBlank()) "No daemon URL"
                                else "Stream off",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
        }
    }
}

private fun perceptionHealthLine(title: String, o: JSONObject?, bridgeEnabled: Boolean): String {
    if (o != null) {
        val ok = o.optBoolean("connected")
        val msg = o.optString("message").trim()
        return if (ok) "$title OK" else "$title: ${msg.take(18)}"
    }
    return if (bridgeEnabled) "$title …" else "$title: off"
}

private fun SlamOccupancyGrid.toOccBitmap(): Bitmap? {
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
