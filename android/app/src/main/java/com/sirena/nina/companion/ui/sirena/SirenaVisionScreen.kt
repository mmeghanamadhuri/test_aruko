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
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
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
import androidx.compose.ui.text.style.TextAlign
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
    /** Immediate feedback from POST /v1/vision/options (e.g. missing ultralytics). */
    var toggleErr by remember { mutableStateOf("") }
    var enrollName by remember { mutableStateOf("") }
    var enrollBusy by remember { mutableStateOf(false) }
    var enrollProgress by remember { mutableStateOf("") }
    var enrollResult by remember { mutableStateOf("") }
    var announceLine by remember { mutableStateOf("") }
    var announceErr by remember { mutableStateOf("") }
    var objectConfidence by remember { mutableStateOf(0.8f) }
    var detectionsText by remember { mutableStateOf<List<String>>(emptyList()) }
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

    LaunchedEffect(visionOn, pipelineOn, objectOn) {
        if (!visionOn || !pipelineOn || !objectOn) {
            detectionsText = emptyList()
            return@LaunchedEffect
        }
        while (true) {
            val j = vm.fetchVisionDetections()
            val arr = j?.optJSONArray("detections")
            val rows = mutableListOf<String>()
            if (arr != null) {
                for (i in 0 until arr.length()) {
                    val o = arr.optJSONObject(i) ?: continue
                    val label = o.optString("label")
                    val conf = o.optDouble("confidence", 0.0)
                    val identity = o.optString("identity").trim()
                    val base = "$label ${(conf * 100).toInt()}%"
                    rows.add(if (identity.isNotEmpty() && !identity.equals("null", ignoreCase = true)) "$base · $identity" else base)
                }
            }
            detectionsText = rows.take(6)
            delay(1200)
        }
    }

    val openCvHint =
        statusMsg.contains("opencv", ignoreCase = true) ||
            statusMsg.contains("cv2", ignoreCase = true)

    LaunchedEffect(faceOn, objectOn, visionOn, pipelineOn) {
        if (!visionOn || !pipelineOn) {
            toggleErr = ""
            return@LaunchedEffect
        }
        val resp = vm.postVisionOptionsSync(face = faceOn, objects = objectOn, objectConfidence = null)
        val faceE = resp.toggleErr("toggle_face_error")
        val objE = resp.toggleErr("toggle_object_error")
        toggleErr = listOfNotNull(faceE, objE).joinToString("\n")
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

    SirenaScrollableScreen(
        titleBar = "Nina · Vision",
        breadcrumb = "Nina / Vision",
        modifier = modifier,
    ) {
        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "Camera & recognition",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.weight(1f),
            )
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Surface(
                    shape = RoundedCornerShape(999.dp),
                    color =
                        if (pipelineOn) MaterialTheme.colorScheme.primaryContainer
                        else MaterialTheme.colorScheme.surfaceVariant,
                ) {
                    Text(
                        "MJPEG",
                        Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.Medium,
                    )
                }
                Surface(
                    shape = RoundedCornerShape(999.dp),
                    color =
                        if (faceOn || objectOn) MaterialTheme.colorScheme.primaryContainer
                        else MaterialTheme.colorScheme.surfaceVariant,
                ) {
                    Text(
                        "Overlays",
                        Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.Medium,
                    )
                }
            }
        }

        if (!visionOn) {
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.55f)),
                elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
            ) {
                Text(
                    "Vision bridge is off on the Jetson — set NINA_LINK_ENABLE_VISION_BRIDGE=1 and install OpenCV + sirena_ui vision dependencies, then restart nina-link.",
                    Modifier.padding(16.dp),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onErrorContainer,
                )
            }
        }

        if (visionOn) {
        Card(
            Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
        ) {
            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                // Title + compact picture-in-corner (fixed width, native 16:9 — avoids full-width stretch).
                Row(
                    Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    verticalAlignment = Alignment.Top,
                ) {
                    Text(
                        "Live preview",
                        modifier = Modifier.weight(1f),
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.primary,
                    )
                    Surface(
                        modifier = Modifier.width(200.dp),
                        shape = RoundedCornerShape(12.dp),
                        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.4f),
                    ) {
                        Box(
                            Modifier
                                .fillMaxWidth()
                                .padding(4.dp)
                                .aspectRatio(16f / 9f),
                            contentAlignment = Alignment.Center,
                        ) {
                            if (pipelineOn && streamRoot.isNotBlank()) {
                                val streamUrl = "$streamRoot/v1/vision/stream"
                                val html =
                                    remember(streamUrl) {
                                        "<html><head><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/></head>" +
                                            "<body style=\"margin:0;background:#000;\">" +
                                            "<img src=\"$streamUrl\" width=\"100%\" style=\"display:block;object-fit:contain;\" />" +
                                            "</body></html>"
                                    }
                                MjpegWebView(html = html)
                            } else {
                                Text(
                                    "Off",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    textAlign = TextAlign.Center,
                                    modifier = Modifier.padding(8.dp),
                                )
                            }
                        }
                    }
                }
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
                if (toggleErr.isNotBlank()) {
                    Text(
                        toggleErr,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
                if (statusMsg.isNotBlank()) {
                    Text(
                        statusMsg,
                        modifier = Modifier.fillMaxWidth(),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
        }

        if (visionOn) {
        Card(
            Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
        ) {
            Column(
                Modifier
                    .fillMaxWidth()
                    .padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text(
                    "Recognition pipeline",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.primary,
                )
                ToggleRow("Face detection", faceOn) { faceOn = it }
                ToggleRow("Object detection", objectOn) { objectOn = it }
                Text(
                    "Object confidence ${(objectConfidence * 100).toInt()}%",
                    modifier = Modifier.fillMaxWidth(),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Slider(
                    modifier = Modifier.fillMaxWidth(),
                    value = objectConfidence,
                    onValueChange = { objectConfidence = it.coerceIn(0.5f, 0.99f) },
                    valueRange = 0.5f..0.99f,
                    enabled = visionOn && pipelineOn && objectOn,
                )
                Button(
                    modifier = Modifier.fillMaxWidth(),
                    onClick = {
                        scope.launch {
                            vm.postVisionOptionsSync(face = faceOn, objects = objectOn, objectConfidence = objectConfidence.toDouble())
                        }
                    },
                    enabled = visionOn && pipelineOn && objectOn,
                ) {
                    Text("Apply confidence")
                }
            }
        }

        Text(
            "Face enrollment",
            modifier = Modifier.fillMaxWidth(),
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.primary,
        )
        Card(
            Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
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
                                val (start, enrollNetErr) = vm.visionEnroll(name, 8)
                                if (enrollNetErr != null) {
                                    enrollResult = enrollNetErr
                                    return@launch
                                }
                                if (start == null) {
                                    enrollResult = "Could not reach the Jetson."
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

        Text(
            "Detections & voice",
            modifier = Modifier.fillMaxWidth(),
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.primary,
        )
        Card(
            Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.4f)),
            elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
        ) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                if (detectionsText.isEmpty()) {
                    Text(
                        "No detections yet.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                } else {
                    detectionsText.forEach {
                        Text(it, style = MaterialTheme.typography.bodySmall)
                    }
                }
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
                    modifier = Modifier.fillMaxWidth(),
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
                            val errJ = vm.fetchVisionAnnounceStatus()
                            val err =
                                errJ?.takeIf { !it.isNull("error") }?.optString("error")?.trim()
                                    ?.takeIf { it.isNotEmpty() && !it.equals("null", ignoreCase = true) }
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
}

private fun JSONObject?.toggleErr(key: String): String? {
    val j = this ?: return null
    if (!j.has(key) || j.isNull(key)) return null
    val s = j.optString(key).trim()
    if (s.isEmpty() || s.equals("null", ignoreCase = true)) return null
    return s
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
        modifier = Modifier.fillMaxSize(),
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
