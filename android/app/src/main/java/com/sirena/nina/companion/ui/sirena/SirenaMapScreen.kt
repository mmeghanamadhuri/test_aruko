package com.sirena.nina.companion.ui.sirena

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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.ui.theme.SirenaSwitch
import kotlinx.coroutines.delay
import org.json.JSONObject

@Composable
fun SirenaMapScreen(
    vm: CompanionViewModel,
    daemonUrl: String?,
    caps: JSONObject?,
    modifier: Modifier = Modifier,
) {
    var autonomyOn by remember { mutableStateOf(false) }
    var mappingOn by remember { mutableStateOf(false) }
    var visionStatus by remember { mutableStateOf<JSONObject?>(null) }
    val visionEnabled = caps?.optBoolean("vision_bridge_enabled") == true

    LaunchedEffect(daemonUrl, visionEnabled) {
        if (daemonUrl.isNullOrBlank() || !visionEnabled) return@LaunchedEffect
        while (true) {
            visionStatus = vm.fetchVisionStatus()
            delay(2000)
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
                    Text("SLAM: pending API", Modifier.padding(horizontal = 8.dp, vertical = 4.dp), style = MaterialTheme.typography.labelSmall)
                }
                Surface(shape = RoundedCornerShape(999.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                    Text(if (autonomyOn) "Autonomy: ON" else "Autonomy: OFF", Modifier.padding(horizontal = 8.dp, vertical = 4.dp), style = MaterialTheme.typography.labelSmall)
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
                    Text(
                        if (mappingOn) "Mapping requested (waiting for endpoint)" else "Occupancy / pose map — endpoint pending",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
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
                        Text("x —  y —  θ —", style = MaterialTheme.typography.bodySmall)
                        Text(
                            visionStatus?.optString("message").orEmpty().ifBlank { "No SLAM telemetry from daemon yet." },
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
                            SirenaSwitch(checked = autonomyOn, onCheckedChange = { autonomyOn = it })
                        }
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Button(onClick = { mappingOn = true }, modifier = Modifier.weight(1f)) { Text("Start") }
                            OutlinedButton(onClick = { mappingOn = false }, modifier = Modifier.weight(1f)) { Text("Stop") }
                        }
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            OutlinedButton(onClick = {}, modifier = Modifier.weight(1f)) { Text("Save map") }
                            OutlinedButton(onClick = {}, modifier = Modifier.weight(1f)) { Text("Clear") }
                        }
                    }
                }
            }
        }
    }
}
