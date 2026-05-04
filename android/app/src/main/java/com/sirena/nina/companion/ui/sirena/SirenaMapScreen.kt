package com.sirena.nina.companion.ui.sirena

import android.graphics.Bitmap
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
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
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.text.font.FontWeight
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
    var autonomyOn by remember { mutableStateOf(false) }
    var mappingOn by remember { mutableStateOf(false) }
    var visionStatus by remember { mutableStateOf<JSONObject?>(null) }
    var slamStatus by remember { mutableStateOf<JSONObject?>(null) }
    var snap by remember { mutableStateOf<JSONObject?>(null) }
    var occBitmap by remember { mutableStateOf<Bitmap?>(null) }
    var mapHint by remember { mutableStateOf("") }
    var autonomyMsg by remember { mutableStateOf("") }
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
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    if (slamOn && occBitmap != null) {
                        Image(
                            bitmap = occBitmap!!.asImageBitmap(),
                            contentDescription = "SLAM occupancy",
                            modifier = Modifier
                                .fillMaxSize()
                                .padding(4.dp),
                        )
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
                                            autonomyMsg = r?.optString("error").orEmpty().ifBlank { "autonomy request failed" }
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
                                onClick = { mappingOn = true },
                                enabled = slamOn,
                                modifier = Modifier.weight(1f),
                            ) { Text("Start") }
                            OutlinedButton(
                                onClick = { mappingOn = false },
                                enabled = slamOn,
                                modifier = Modifier.weight(1f),
                            ) { Text("Stop") }
                        }
                        Text(
                            "Mapping runs with the SLAM bridge; use Save on-robot if you add a file endpoint later.",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            OutlinedButton(onClick = {}, enabled = false, modifier = Modifier.weight(1f)) { Text("Save map") }
                            OutlinedButton(onClick = {}, enabled = false, modifier = Modifier.weight(1f)) { Text("Clear") }
                        }
                    }
                }
            }
        }
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
