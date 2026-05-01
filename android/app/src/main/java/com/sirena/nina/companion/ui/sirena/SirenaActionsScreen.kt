package com.sirena.nina.companion.ui.sirena

import android.media.MediaPlayer
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
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
            Card(
                Modifier
                    .weight(0.38f)
                    .fillMaxHeight(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f)),
            ) {
                Column(
                    Modifier
                        .fillMaxSize()
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
                                .weight(1f)
                                .clip(RoundedCornerShape(10.dp)),
                        contentScale = ContentScale.Crop,
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
            Column(
                Modifier
                    .weight(0.62f)
                    .fillMaxHeight(),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                when (selectedTab) {
                    0 ->
                        PlaybackTab(
                            manifestActions = manifestActions,
                            onPlayAction = onPlayAction,
                        )

                    1 -> RecordTabContent(vm = vm, caps = caps)
                    2 ->
                        AudioTabContent(
                            vm = vm,
                            caps = caps,
                            withAudio = manifestActions.filter { !it.audio.isNullOrBlank() },
                            onPlayAction = onPlayAction,
                        )
                }
            }
        }
    }
}

@Composable
private fun PlaybackTab(
    manifestActions: List<ActionRowUi>,
    onPlayAction: (String) -> Unit,
) {
    Column(Modifier.fillMaxSize()) {
        Text("Playback", fontWeight = FontWeight.SemiBold)
        Text(
            "Registered motions from the robot manifest. Tap Play to queue on the Jetson (requires action bridge).",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 8.dp),
        )
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
                            horizontalArrangement = Arrangement.SpaceBetween,
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
                            Button(onClick = { onPlayAction(row.name) }) {
                                Text("Play")
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
        Button(
            onClick = {
                scope.launch {
                    statusLine = "Starting…"
                    val err =
                        vm.startRemoteRecord(
                            name = name.trim(),
                            seconds = 5.0,
                            hz = 20.0,
                            countdown = 3.0,
                            holdAfter = false,
                            register = true,
                        )
                    if (err != null) {
                        statusLine = err
                    } else {
                        poll = true
                    }
                }
            },
            modifier = Modifier.padding(top = 8.dp),
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

@Composable
private fun AudioTabContent(
    vm: CompanionViewModel,
    caps: JSONObject?,
    withAudio: List<ActionRowUi>,
    onPlayAction: (String) -> Unit,
) {
    val scope = rememberCoroutineScope()
    val staticOn = caps?.optBoolean("actions_static_enabled") == true

    Column(Modifier.fillMaxSize()) {
        Text("Audio", fontWeight = FontWeight.SemiBold)
        Text(
            "Clips live under ``nina/actions/audio/``. Motion playback runs on the Jetson when you tap Play motion. " +
                "Preview requires ``NINA_LINK_ENABLE_ACTIONS_STATIC=1``.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 8.dp),
        )
        if (!staticOn) {
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.35f))) {
                Text(
                    "HTTP audio preview disabled — enable NINA_LINK_ENABLE_ACTIONS_STATIC on the Jetson to stream clips to the tablet.",
                    Modifier.padding(12.dp),
                    style = MaterialTheme.typography.bodySmall,
                )
            }
        }
        if (withAudio.isEmpty()) {
            Text(
                "No audio-linked entries in the manifest.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(withAudio, key = { it.name }) { row ->
                    Card(
                        Modifier.fillMaxWidth(),
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                    ) {
                        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Row(
                                Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Column(Modifier.weight(1f)) {
                                    Text(row.name, fontWeight = FontWeight.SemiBold)
                                    Text(
                                        row.audio ?: "",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                                Button(onClick = { onPlayAction(row.name) }) {
                                    Text("Play motion")
                                }
                            }
                            if (staticOn && !row.audio.isNullOrBlank()) {
                                OutlinedButton(
                                    onClick = {
                                        scope.launch {
                                            val url = vm.mediaFileUrl(row.audio!!)
                                            try {
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
                                        }
                                    },
                                    modifier = Modifier.fillMaxWidth(),
                                ) {
                                    Text("Preview clip on tablet")
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
