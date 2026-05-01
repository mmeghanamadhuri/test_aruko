package com.sirena.nina.companion.ui.sirena

import android.media.MediaPlayer
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import com.sirena.nina.companion.ui.theme.SirenaSwitch
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.ActionRowUi
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.R
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/**
 * Mirrors [sirena_ui.screens.actions_screen.ActionsScreen] —
 * Playback lists manifest actions from the Jetson; Record/Audio match desktop roles.
 */
@Composable
fun SirenaActionsScreen(
    selectedTab: Int,
    onTabSelected: (Int) -> Unit,
    manifestActions: List<ActionRowUi>,
    manifestError: String?,
    onRefreshManifest: () -> Unit,
    onPlayAction: (String) -> Unit,
    vm: CompanionViewModel,
    caps: JSONObject?,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier
            .fillMaxSize()
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            Text(
                "Nina · Actions",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Surface(shape = RoundedCornerShape(999.dp), color = MaterialTheme.colorScheme.surfaceVariant) {
                Text(
                    if (manifestError != null) "List error" else "${manifestActions.size} actions",
                    Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                    style = MaterialTheme.typography.labelSmall,
                    color =
                        if (manifestError != null) {
                            MaterialTheme.colorScheme.error
                        } else {
                            MaterialTheme.colorScheme.onSurfaceVariant
                        },
                )
            }
        }

        manifestError?.let {
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(
                    it,
                    Modifier.padding(12.dp),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onErrorContainer,
                )
            }
        }

        TabRow(selectedTabIndex = selectedTab) {
            SIRENA_ACTIONS_SUBTAB_LABELS.forEachIndexed { index, label ->
                Tab(
                    selected = selectedTab == index,
                    onClick = { onTabSelected(index) },
                    text = { Text(label) },
                )
            }
        }

        Row(
            Modifier
                .fillMaxWidth()
                .weight(1f),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Box(Modifier.weight(0.38f).fillMaxHeight()) {
                ActionsHeroCard(onRefreshManifest = onRefreshManifest)
            }
            Column(
                Modifier
                    .weight(0.62f)
                    .fillMaxHeight(),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                when (selectedTab) {
                    0 ->
                        PlaybackTab(
                            vm = vm,
                            manifestActions = manifestActions,
                            onPlayAction = onPlayAction,
                            onRefreshManifest = onRefreshManifest,
                        )

                    1 -> RecordTabContent(vm = vm, caps = caps)
                    2 ->
                        AudioTabContent(
                            vm = vm,
                            caps = caps,
                            manifestActions = manifestActions,
                            onPlayAction = onPlayAction,
                            onRefreshManifest = onRefreshManifest,
                        )
                }
            }
        }
    }
}

@Composable
private fun ActionsHeroCard(onRefreshManifest: () -> Unit) {
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f)),
    ) {
        Column(
            Modifier
                .fillMaxWidth()
                .padding(12.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Image(
                painter = painterResource(R.drawable.nina_hero),
                contentDescription = "Nina",
                modifier =
                    Modifier
                        .fillMaxWidth()
                        .aspectRatio(4f / 3f)
                        .clip(RoundedCornerShape(10.dp)),
                contentScale = ContentScale.Fit,
            )
            Text("Nina", fontWeight = FontWeight.Bold)
            Text(
                "Manifest from Jetson GET /v1/actions.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedButton(onClick = onRefreshManifest, modifier = Modifier.fillMaxWidth()) {
                Text("Refresh list")
            }
        }
    }
}

@Composable
private fun PlaybackTab(
    vm: CompanionViewModel,
    manifestActions: List<ActionRowUi>,
    onPlayAction: (String) -> Unit,
    onRefreshManifest: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    var pendingDelete by remember { mutableStateOf<String?>(null) }
    var deleteErr by remember { mutableStateOf<String?>(null) }

    pendingDelete?.let { name ->
        AlertDialog(
            onDismissRequest = { pendingDelete = null },
            title = { Text("Remove action") },
            text = {
                Text(
                    "Remove \"$name\" from the Jetson manifest and delete its recording file? " +
                        "Requires ``NINA_LINK_ENABLE_ACTIONS_STATIC=1`` and auth.",
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        scope.launch {
                            deleteErr = vm.deleteManifestAction(name, deleteRecording = true, deleteAudio = false)
                            pendingDelete = null
                            if (deleteErr == null) {
                                onRefreshManifest()
                            }
                        }
                    },
                ) {
                    Text("Delete", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = {
                TextButton(onClick = { pendingDelete = null }) {
                    Text("Cancel")
                }
            },
        )
    }

    Column(Modifier.fillMaxSize()) {
        Text("Playback", fontWeight = FontWeight.SemiBold)
        Text(
            "Registered motions from the robot manifest. Play queues motion on the Jetson; Delete removes the manifest entry.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 8.dp),
        )
        deleteErr?.let {
            Text(
                it,
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(bottom = 6.dp),
            )
        }
        if (manifestActions.isEmpty()) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(
                    "No actions loaded — refresh or check Jetson nina-link.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        } else {
            LazyColumn(
                verticalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.fillMaxSize(),
            ) {
                items(manifestActions, key = { it.name }) { row ->
                    Card(
                        Modifier.fillMaxWidth(),
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                    ) {
                        Row(
                            Modifier
                                .fillMaxWidth()
                                .padding(12.dp),
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(Modifier.weight(1f)) {
                                Text(row.name, fontWeight = FontWeight.SemiBold)
                                row.file?.let {
                                    Text(
                                        it,
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        maxLines = 1,
                                        overflow = TextOverflow.Ellipsis,
                                    )
                                }
                            }
                            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                Button(onClick = { onPlayAction(row.name) }) {
                                    Text("Play")
                                }
                                OutlinedButton(onClick = { pendingDelete = row.name }) {
                                    Text("Delete")
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun RecordTabContent(vm: CompanionViewModel, caps: JSONObject?) {
    val scope = rememberCoroutineScope()
    var name by remember { mutableStateOf("demo_motion") }
    var secondsStr by remember { mutableStateOf("5") }
    var hzStr by remember { mutableStateOf("20") }
    var countdownStr by remember { mutableStateOf("3") }
    var holdAfter by remember { mutableStateOf(false) }
    var registerInManifest by remember { mutableStateOf(true) }
    var statusLine by remember { mutableStateOf("") }
    var poll by remember { mutableStateOf(false) }
    val recordOn = caps?.optBoolean("record_bridge_enabled") == true

    LaunchedEffect(poll) {
        if (!poll) return@LaunchedEffect
        while (true) {
            val j = vm.fetchRecordStatus()
            if (j == null) {
                delay(500)
                continue
            }
            val ph = j.optString("phase", "idle")
            val err = j.optString("error").takeIf { it.isNotBlank() }
            val extra =
                buildString {
                    if (j.has("samples_done") && !j.isNull("samples_done")) {
                        append(" samples ${j.optInt("samples_done")}")
                        if (j.has("samples_total") && !j.isNull("samples_total")) {
                            append("/${j.optInt("samples_total")}")
                        }
                    }
                    if (j.has("countdown_remaining_sec")) {
                        append(" · countdown ${j.optInt("countdown_remaining_sec")}s")
                    }
                }
            statusLine =
                when {
                    err != null -> "Error: $err"
                    ph == "idle" && j.optString("last_saved").isNotBlank() ->
                        "Saved: ${j.optString("last_saved")}"

                    else -> "Phase: $ph$extra"
                }
            if (ph == "idle") {
                poll = false
                vm.refreshManifestActions()
                break
            }
            delay(450)
        }
    }

    Column(Modifier.verticalScroll(rememberScrollState())) {
        Text("Record", fontWeight = FontWeight.SemiBold)
        Text(
            "Captures poses into ``nina/actions/recordings/`` on the Jetson and can register the clip in the manifest. " +
                "Requires ``NINA_LINK_ENABLE_RECORD_BRIDGE=1`` and no other bus owner (stop Sirena motion UI).",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 8.dp),
        )
        if (!recordOn) {
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.4f))) {
                Text(
                    "Record bridge is off on the Jetson — set NINA_LINK_ENABLE_RECORD_BRIDGE=1 and restart nina-link.",
                    Modifier.padding(16.dp),
                    style = MaterialTheme.typography.bodySmall,
                )
            }
            return@Column
        }
        OutlinedTextField(
            value = name,
            onValueChange = { name = it },
            label = { Text("Action name") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
            keyboardOptions =
                KeyboardOptions(
                    capitalization = KeyboardCapitalization.None,
                ),
        )
        OutlinedTextField(
            value = secondsStr,
            onValueChange = { secondsStr = it },
            label = { Text("Duration (seconds)") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
        )
        OutlinedTextField(
            value = hzStr,
            onValueChange = { hzStr = it },
            label = { Text("Sample rate (Hz)") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
        )
        OutlinedTextField(
            value = countdownStr,
            onValueChange = { countdownStr = it },
            label = { Text("Countdown (seconds)") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
        )
        Row(
            Modifier.fillMaxWidth().padding(top = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text("Register in manifest", style = MaterialTheme.typography.bodyMedium)
                Text(
                    "Adds the new recording to manifest.json when capture finishes.",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            SirenaSwitch(checked = registerInManifest, onCheckedChange = { registerInManifest = it })
        }
        Row(
            Modifier.fillMaxWidth().padding(top = 4.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text("Re-engage torque after recording", style = MaterialTheme.typography.bodyMedium)
                Text(
                    "Hold servos after capture (matches desktop Record panel).",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            SirenaSwitch(checked = holdAfter, onCheckedChange = { holdAfter = it })
        }
        Button(
            onClick = {
                scope.launch {
                    val sec = secondsStr.trim().toDoubleOrNull()
                    val hz = hzStr.trim().toDoubleOrNull()
                    val cd = countdownStr.trim().toDoubleOrNull()
                    if (sec == null || hz == null || cd == null) {
                        statusLine = "Enter valid numbers for duration, Hz, and countdown."
                        return@launch
                    }
                    statusLine = "Starting…"
                    val err =
                        vm.startRemoteRecord(
                            name = name.trim(),
                            seconds = sec,
                            hz = hz,
                            countdown = cd,
                            holdAfter = holdAfter,
                            register = registerInManifest,
                        )
                    if (err != null) {
                        statusLine = err
                    } else {
                        poll = true
                    }
                }
            },
            modifier = Modifier.padding(top = 12.dp),
            enabled = name.trim().length >= 2,
        ) {
            Text("Start recording")
        }
        if (statusLine.isNotBlank()) {
            Text(
                statusLine,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(top = 8.dp),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

private data class VoicePreset(val label: String, val lang: String, val tld: String)

private val SIRENA_VOICE_PRESETS: List<VoicePreset> =
    listOf(
        VoicePreset("US English (default)", "en", "com"),
        VoicePreset("UK English", "en", "co.uk"),
        VoicePreset("Australian English", "en", "com.au"),
        VoicePreset("Indian English", "en", "co.in"),
        VoicePreset("Hindi", "hi", "co.in"),
        VoicePreset("Spanish (Spain)", "es", "es"),
        VoicePreset("French (France)", "fr", "fr"),
    )

@Composable
private fun AudioTabContent(
    vm: CompanionViewModel,
    caps: JSONObject?,
    manifestActions: List<ActionRowUi>,
    onPlayAction: (String) -> Unit,
    onRefreshManifest: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val staticOn = caps?.optBoolean("actions_static_enabled") == true
    val names = remember(manifestActions) { manifestActions.map { it.name }.sorted() }
    var selectedName by remember { mutableStateOf("") }
    LaunchedEffect(names) {
        if (selectedName.isBlank() && names.isNotEmpty()) {
            selectedName = names.first()
        }
        if (selectedName.isNotBlank() && selectedName !in names) {
            selectedName = names.firstOrNull() ?: ""
        }
    }

    var offsetStr by remember { mutableStateOf("0") }
    var speakText by remember { mutableStateOf("") }
    var showActionPicker by remember { mutableStateOf(false) }
    var showVoicePicker by remember { mutableStateOf(false) }
    var voiceIndex by remember { mutableIntStateOf(0) }
    val preset = SIRENA_VOICE_PRESETS[voiceIndex.coerceIn(0, SIRENA_VOICE_PRESETS.lastIndex)]

    var audioRel by remember { mutableStateOf<String?>(null) }
    var clipExists by remember { mutableStateOf(false) }
    var infoLoading by remember { mutableStateOf(false) }
    var infoLine by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf("") }
    var reloadAudioTick by remember { mutableIntStateOf(0) }

    LaunchedEffect(selectedName, reloadAudioTick) {
        if (selectedName.isBlank()) return@LaunchedEffect
        infoLoading = true
        infoLine = ""
        val j = vm.fetchActionAudioInfo(selectedName)
        infoLoading = false
        if (j == null) {
            infoLine = "Could not load audio info from nina-link."
            audioRel = null
            clipExists = false
            return@LaunchedEffect
        }
        audioRel = j.optString("audio_rel").takeIf { it.isNotBlank() }
        clipExists = j.optBoolean("clip_file_exists")
        val off = j.optDouble("audio_offset")
        offsetStr =
            if (j.has("audio_offset") && !j.isNull("audio_offset") && !off.isNaN()) {
                String.format("%.2f", off).trimEnd('0').trimEnd('.').ifEmpty { "0" }
            } else {
                "0"
            }
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState()),
    ) {
        Text("Audio", fontWeight = FontWeight.SemiBold)
        Text(
            "Match desktop Audio panel: pick an action, set offset, generate speech (gTTS on Jetson), or clear mapping. " +
                "Editing requires ``NINA_LINK_ENABLE_ACTIONS_STATIC=1`` and a bearer token when the daemon uses one.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 8.dp),
        )
        if (!staticOn) {
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.35f))) {
                Text(
                    "Manifest edits + preview need ``NINA_LINK_ENABLE_ACTIONS_STATIC=1`` on the Jetson (same as HTTP clip preview).",
                    Modifier.padding(12.dp),
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
        if (names.isEmpty()) {
            Text(
                "No actions in the manifest — refresh from Playback.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            return@Column
        }

        Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = selectedName,
                onValueChange = {},
                readOnly = true,
                label = { Text("Action") },
                modifier = Modifier.weight(1f),
            )
            OutlinedButton(onClick = { showActionPicker = true }) {
                Text("Pick…")
            }
        }

        if (showActionPicker) {
            AlertDialog(
                onDismissRequest = { showActionPicker = false },
                title = { Text("Choose action") },
                text = {
                    LazyColumn(Modifier.heightIn(max = 400.dp)) {
                        items(names, key = { it }) { n ->
                            TextButton(
                                onClick = {
                                    selectedName = n
                                    showActionPicker = false
                                },
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                Text(n)
                            }
                        }
                    }
                },
                confirmButton = {
                    TextButton(onClick = { showActionPicker = false }) {
                        Text("Cancel")
                    }
                },
            )
        }

        Row(
            Modifier.fillMaxWidth().padding(top = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Button(
                onClick = { onPlayAction(selectedName) },
                enabled = selectedName.isNotBlank(),
            ) {
                Text("Play motion")
            }
            OutlinedButton(
                onClick = {
                    scope.launch {
                        busy = "Refreshing manifest…"
                        onRefreshManifest()
                        busy = ""
                    }
                },
            ) {
                Text("Refresh manifest")
            }
        }

        Text(
            buildString {
                append("Clip: ")
                append(audioRel ?: "—")
                if (audioRel != null) append(if (clipExists) " (file on Jetson)" else " (file missing)")
                if (infoLoading) append(" · loading…")
            },
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 8.dp),
        )
        OutlinedTextField(
            value = offsetStr,
            onValueChange = { offsetStr = it },
            label = { Text("Audio offset (s)") },
            supportingText = {
                Text("Seconds after motion starts before the clip plays (matches desktop).")
            },
            singleLine = true,
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
            enabled = staticOn,
        )

        Row(
            Modifier.fillMaxWidth().padding(top = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedButton(
                onClick = {
                    scope.launch {
                        if (!staticOn || audioRel.isNullOrBlank()) return@launch
                        busy = "Preview…"
                        try {
                            val url = vm.mediaFileUrl(audioRel!!)
                            val mp =
                                withContext(Dispatchers.IO) {
                                    MediaPlayer().apply {
                                        setDataSource(url)
                                        prepare()
                                    }
                                }
                            withContext(Dispatchers.Main) {
                                mp.start()
                                mp.setOnCompletionListener { it.release() }
                            }
                        } catch (_: Exception) {
                        }
                        busy = ""
                    }
                },
                enabled = staticOn && clipExists && !audioRel.isNullOrBlank(),
            ) {
                Text("Play clip")
            }
            OutlinedButton(
                onClick = {
                    scope.launch {
                        val off = offsetStr.trim().toDoubleOrNull()
                        if (off == null) {
                            infoLine = "Enter a valid offset."
                            return@launch
                        }
                        busy = "Saving offset…"
                        val err = vm.postActionAudioOffset(selectedName, off)
                        busy = ""
                        infoLine = err ?: "Offset saved."
                        onRefreshManifest()
                        reloadAudioTick++
                    }
                },
                enabled = staticOn && !audioRel.isNullOrBlank(),
            ) {
                Text("Save offset")
            }
        }

        Row(
            Modifier
                .fillMaxWidth()
                .padding(top = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = preset.label,
                onValueChange = {},
                readOnly = true,
                label = { Text("Voice") },
                modifier = Modifier.weight(1f),
            )
            OutlinedButton(onClick = { showVoicePicker = true }) {
                Text("Pick…")
            }
        }

        if (showVoicePicker) {
            AlertDialog(
                onDismissRequest = { showVoicePicker = false },
                title = { Text("Voice") },
                text = {
                    LazyColumn(Modifier.heightIn(max = 400.dp)) {
                        items(SIRENA_VOICE_PRESETS.size) { idx ->
                            val p = SIRENA_VOICE_PRESETS[idx]
                            TextButton(
                                onClick = {
                                    voiceIndex = idx
                                    showVoicePicker = false
                                },
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                Text(p.label)
                            }
                        }
                    }
                },
                confirmButton = {
                    TextButton(onClick = { showVoicePicker = false }) {
                        Text("Cancel")
                    }
                },
            )
        }

        OutlinedTextField(
            value = speakText,
            onValueChange = { speakText = it },
            label = { Text("Text to speak") },
            modifier =
                Modifier
                    .fillMaxWidth()
                    .padding(top = 8.dp),
            minLines = 2,
        )

        Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                onClick = {
                    scope.launch {
                        val t = speakText.trim()
                        if (t.isEmpty()) {
                            infoLine = "Enter text to generate."
                            return@launch
                        }
                        busy = "Generating on Jetson…"
                        val off = offsetStr.trim().toDoubleOrNull() ?: 0.0
                        val err =
                            vm.postActionAudioGenerate(
                                selectedName,
                                t,
                                preset.lang,
                                preset.tld,
                                off,
                            )
                        busy = ""
                        infoLine = err ?: "Generated and saved to audio/${selectedName}.mp3"
                        onRefreshManifest()
                        reloadAudioTick++
                    }
                },
                enabled = staticOn && selectedName.isNotBlank(),
            ) {
                Text("Generate & save")
            }
            OutlinedButton(
                onClick = {
                    scope.launch {
                        busy = "Removing audio mapping…"
                        val err = vm.postActionAudioClear(selectedName)
                        busy = ""
                        infoLine = err ?: "Audio mapping cleared."
                        onRefreshManifest()
                        reloadAudioTick++
                    }
                },
                enabled = staticOn && selectedName.isNotBlank(),
            ) {
                Text("Remove audio")
            }
        }

        if (busy.isNotBlank()) {
            Text(busy, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 8.dp))
        }
        if (infoLine.isNotBlank()) {
            Text(
                infoLine,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 4.dp),
            )
        }
    }
}
