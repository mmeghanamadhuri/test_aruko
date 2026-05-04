package com.sirena.nina.companion.ui.sirena

import android.annotation.SuppressLint
import android.webkit.WebView
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.ui.theme.SirenaSwitch
import kotlinx.coroutines.delay
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
    var cameraOn by remember { mutableStateOf(false) }
    var autonomyOn by remember { mutableStateOf(false) }
    var visionStatus by remember { mutableStateOf<JSONObject?>(null) }
    var statusLine by remember { mutableStateOf("") }

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
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Pill("Lidar: pending")
                Pill("Depth: pending")
                Pill(if (cameraOn) "Cam: live" else "Cam: off")
            }
        }

        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Autonomous", style = MaterialTheme.typography.bodyMedium)
            SirenaSwitch(checked = autonomyOn, onCheckedChange = { autonomyOn = it }, enabled = true)
        }
        Text(
            "Autonomy API is not exposed by nina-link yet; this mirrors operator intent while map/perception APIs are phased in.",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        Row(
            Modifier
                .fillMaxWidth()
                .height(260.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            SensorPane(
                title = "LiDAR",
                subtitle = "SLAM occupancy feed pending daemon endpoint",
                modifier = Modifier.weight(1f),
            )
            CameraPane(
                title = "RGB",
                streamRoot = streamRoot,
                enabled = cameraOn && visionOn && streamRoot.isNotBlank(),
                modifier = Modifier.weight(1f),
            )
            SensorPane(
                title = "Depth",
                subtitle = "Depth visualization pending daemon endpoint",
                modifier = Modifier.weight(1f),
            )
        }

        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
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
private fun SensorPane(title: String, subtitle: String, modifier: Modifier = Modifier) {
    Card(modifier.fillMaxSize(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            Box(Modifier.fillMaxWidth().aspectRatio(16f / 9f), contentAlignment = Alignment.Center) {
                Surface(
                    Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.55f),
                    shape = MaterialTheme.shapes.medium,
                ) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
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
private fun CameraPane(title: String, streamRoot: String, enabled: Boolean, modifier: Modifier = Modifier) {
    Card(modifier.fillMaxSize(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(title, fontWeight = FontWeight.SemiBold)
            Box(Modifier.fillMaxWidth().aspectRatio(16f / 9f), contentAlignment = Alignment.Center) {
                if (enabled) {
                    val streamUrl = "$streamRoot/v1/vision/stream"
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
                            Text("Turn on camera stream", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
        }
    }
}
