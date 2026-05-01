package com.sirena.nina.companion.ui.sirena

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
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.ActionRowUi

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
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                        modifier = Modifier.padding(12.dp),
                    ) {
                        Text("Nina", fontWeight = FontWeight.Bold)
                        Text(
                            "Actions match ``nina/actions/manifest.json`` on the Jetson via GET /v1/actions.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        OutlinedButton(onClick = onRefreshManifest) {
                            Text("Refresh list")
                        }
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

                    1 -> RecordTabContent()
                    2 ->
                        AudioTabContent(
                            manifestActions.filter { !it.audio.isNullOrBlank() },
                            onPlayAction,
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
private fun RecordTabContent() {
    Column(Modifier.verticalScroll(rememberScrollState())) {
        Text("Record", fontWeight = FontWeight.SemiBold)
        Text(
            "Recording captures poses into ``nina/actions/recordings/`` on the Jetson and updates the manifest. " +
                "Use the robot's Sirena UI or ``python -m nina.app record-action`` over SSH for now.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 8.dp),
        )
        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.4f))) {
            Text(
                "Companion recording controls can be added once the link daemon exposes record endpoints.",
                Modifier.padding(16.dp),
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun AudioTabContent(
    withAudio: List<ActionRowUi>,
    onPlayAction: (String) -> Unit,
) {
    Column(Modifier.fillMaxSize()) {
        Text("Audio", fontWeight = FontWeight.SemiBold)
        Text(
            "Actions with clips under ``nina/actions/audio/``. Playback uses the motion Play button when the bridge is enabled.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(bottom = 8.dp),
        )
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
                        Row(
                            Modifier.padding(12.dp),
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
                    }
                }
            }
        }
    }
}
