package com.sirena.nina.companion.ui.sirena

import android.annotation.SuppressLint
import android.webkit.WebView
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.ui.theme.SirenaSwitch
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONObject

/** Live MJPEG preview + toggles (Jetson ``NINA_LINK_ENABLE_VISION_BRIDGE=1`` + OpenCV / sirena_ui pipeline). */
@Composable
fun SirenaVisionScreen(
    vm: CompanionViewModel,
    daemonUrl: String?,
    caps: JSONObject?,
    modifier: Modifier = Modifier,
) {
    val visionOn = caps?.optBoolean("vision_bridge_enabled") == true
    var pipelineOn by remember { mutableStateOf(false) }
    var faceOn by remember { mutableStateOf(false) }
    var objectOn by remember { mutableStateOf(false) }
    var statusMsg by remember { mutableStateOf("") }
    var enrollName by remember { mutableStateOf("") }
    var enrollBusy by remember { mutableStateOf(false) }
    var enrollProgress by remember { mutableStateOf("") }
    var enrollResult by remember { mutableStateOf("") }
    var announceLine by remember { mutableStateOf("") }
    var announceErr by remember { mutableStateOf("") }
    val scope = rememberCoroutineScope()

    val streamRoot = daemonUrl?.trimEnd('/') ?: ""

    LaunchedEffect(daemonUrl, visionOn) {
        if (!visionOn || daemonUrl.isNullOrBlank()) return@LaunchedEffect
        while (true) {
            val st = vm.fetchVisionStatus()
            statusMsg = st?.optString("message") ?: ""
            delay(2000)
        }
    }

    val openCvHint =
        statusMsg.contains("opencv", ignoreCase = true) ||
            statusMsg.contains("cv2", ignoreCase = true)

    LaunchedEffect(faceOn, objectOn, visionOn, pipelineOn) {
        if (!visionOn || !pipelineOn) return@LaunchedEffect
        vm.postVisionOptions(face = faceOn, objects = objectOn, objectConfidence = null)
    }

    LaunchedEffect(pipelineOn, visionOn) {
        if (!visionOn) return@LaunchedEffect
        if (pipelineOn) {
            val err = vm.visionOpen()
            if (err != null) statusMsg = err
        } else {
            vm.visionStop()
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
                "Nina · Vision",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Surface(shape = RoundedCornerShape(999.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                    Text("Live", Modifier.padding(horizontal = 8.dp, vertical = 4.dp), style = MaterialTheme.typography.labelSmall)
                }
                Surface(shape = RoundedCornerShape(999.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                    Text("Overlay", Modifier.padding(horizontal = 8.dp, vertical = 4.dp), style = MaterialTheme.typography.labelSmall)
                }
            }
        }

        if (!visionOn) {
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f))) {
                Text(
                    "Vision bridge is off on the Jetson — set NINA_LINK_ENABLE_VISION_BRIDGE=1 and install OpenCV + sirena_ui vision dependencies, then restart nina-link.",
                    Modifier.padding(16.dp),
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            return@Column
        }

        Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Preview", fontWeight = FontWeight.Bold)
                ToggleRow("Camera stream", pipelineOn) { pipelineOn = it }
                if (openCvHint && pipelineOn) {
                    Card(
                        Modifier
                            .fillMaxWidth()
                            .padding(bottom = 8.dp),
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.45f)),
                    ) {
                        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text("OpenCV not available on the Jetson", fontWeight = FontWeight.SemiBold)
                            Text(
                                "Install it into the same Python environment as nina-link (often ``.venv-link``), then restart the service:\n\n" +
                                    "``./.venv-link/bin/pip install opencv-python-headless``\n\n" +
                                    "``sudo systemctl restart nina-link``",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onErrorContainer,
                            )
                        }
                    }
                }
                if (statusMsg.isNotBlank()) {
                    Text(statusMsg, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                // Same MJPEG resolution from Jetson; constrain display size so the frame fits comfortably on tablet.
                BoxWithConstraints(
                    Modifier.fillMaxWidth(),
                    contentAlignment = Alignment.Center,
                ) {
                    val maxPreviewHeight = 260.dp
                    val widthWhenCapped = maxPreviewHeight * (16f / 9f)
                    val naturalHeight = maxWidth * (9f / 16f)
                    val previewModifier =
                        if (naturalHeight <= maxPreviewHeight) {
                            Modifier
                                .fillMaxWidth()
                                .aspectRatio(16f / 9f)
                        } else {
                            Modifier
                                .width(widthWhenCapped)
                                .height(maxPreviewHeight)
                        }
                    Box(previewModifier, contentAlignment = Alignment.Center) {
                        if (pipelineOn && streamRoot.isNotBlank()) {
                            val streamUrl = "$streamRoot/v1/vision/stream"
                            val html =
                                remember(streamUrl) {
                                    "<html><head><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/></head>" +
                                        "<body style=\"margin:0;background:#000;\">" +
                                        "<img src=\"$streamUrl\" width=\"100%\" style=\"display:block\" />" +
                                        "</body></html>"
                                }
                            MjpegWebView(html = html)
                        } else {
                            Surface(
                                Modifier.fillMaxSize(),
                                shape = RoundedCornerShape(8.dp),
                                color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f),
                            ) {
                                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                                    Text(
                                        "Turn on Camera stream",
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }

        Text("Pipeline", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
        ToggleRow("Face detection", faceOn) { faceOn = it }
        ToggleRow("Object detection", objectOn) { objectOn = it }

        Text("Face enrollment", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
        Card(
            Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        ) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    "Capture 8 face samples and save to the robot (same as Sirena UI). " +
                        "One person in frame, good light, face detection on, camera stream on.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                OutlinedTextField(
                    value = enrollName,
                    onValueChange = { enrollName = it },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    label = { Text("Name to store") },
                    keyboardOptions = KeyboardOptions(
                        capitalization = KeyboardCapitalization.Words,
                    ),
                )
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(
                        onClick = {
                            scope.launch {
                                enrollResult = ""
                                val name = enrollName.trim()
                                if (name.isEmpty()) {
                                    enrollResult = "Enter a name for this face."
                                    return@launch
                                }
                                if (!pipelineOn) {
                                    enrollResult = "Turn on Camera stream first."
                                    return@launch
                                }
                                if (!faceOn) {
                                    enrollResult = "Enable Face detection first."
                                    return@launch
                                }
                                val start = vm.visionEnroll(name, 8)
                                if (start == null) {
                                    enrollResult = "Request failed (network or auth)."
                                    return@launch
                                }
                                if (!start.optBoolean("ok")) {
                                    enrollResult = start.optString("error", "Could not start enrollment.")
                                    return@launch
                                }
                                enrollBusy = true
                                enrollProgress = "0 / 8"
                                while (true) {
                                    delay(400)
                                    val st = vm.fetchVisionEnrollStatus()
                                    if (st == null) {
                                        enrollResult = "Lost status from robot."
                                        enrollBusy = false
                                        break
                                    }
                                    val t = st.optInt("target", 8)
                                    val s = st.optInt("samples", 0)
                                    enrollProgress = "$s / $t"
                                    if (st.optBoolean("in_progress") != true) {
                                        val last = st.optJSONObject("last")
                                        enrollResult = last?.optString("message") ?: ""
                                        enrollBusy = false
                                        break
                                    }
                                }
                            }
                        },
                        enabled = visionOn && !enrollBusy,
                    ) {
                        Text(if (enrollBusy) "Enrolling…" else "Start (8 samples)")
                    }
                }
                if (enrollProgress.isNotBlank()) {
                    Text(enrollProgress, style = MaterialTheme.typography.bodySmall)
                }
                if (enrollResult.isNotBlank()) {
                    Text(enrollResult, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }

        Text("Detections", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
        Card(
            Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.4f)),
        ) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(
                    "Bounding boxes are drawn on the Jetson stream when toggles are on (same pipeline as Sirena UI).",
                    style = MaterialTheme.typography.bodySmall,
                )
                Text(
                    "The robot can speak what it sees using gTTS (install gTTS on the Jetson; audio via mpg123 or similar).",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Button(
                    onClick = {
                        scope.launch {
                            announceErr = ""
                            announceLine = ""
                            if (!pipelineOn) {
                                announceErr = "Turn on Camera stream first."
                                return@launch
                            }
                            if (!objectOn) {
                                announceErr = "Enable Object detection to label the scene."
                                return@launch
                            }
                            val j = vm.visionAnnounceObjects()
                            if (j == null) {
                                announceErr = "Request failed."
                                return@launch
                            }
                            if (!j.optBoolean("ok", true)) {
                                announceErr = j.optString("error", "Failed")
                                return@launch
                            }
                            if (j.optBoolean("skipped")) {
                                announceLine = j.optString("sentence", "")
                                return@launch
                            }
                            announceLine = j.optString("sentence", "")
                            delay(1200)
                            val err = vm.fetchVisionAnnounceStatus()?.optString("error")
                            if (!err.isNullOrBlank()) {
                                announceErr = err
                            }
                        }
                    },
                    enabled = visionOn && pipelineOn && objectOn,
                ) {
                    Text("Speak detected objects")
                }
                if (announceLine.isNotBlank()) {
                    Text(announceLine, style = MaterialTheme.typography.bodySmall)
                }
                if (announceErr.isNotBlank()) {
                    Text(announceErr, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                }
            }
        }
    }
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun MjpegWebView(html: String) {
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
        modifier = Modifier.fillMaxWidth().fillMaxHeight(),
    )
}

@Composable
private fun ToggleRow(label: String, checked: Boolean, onCheckedChange: (Boolean) -> Unit) {
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, style = MaterialTheme.typography.bodyMedium)
        SirenaSwitch(checked = checked, onCheckedChange = onCheckedChange)
    }
}
