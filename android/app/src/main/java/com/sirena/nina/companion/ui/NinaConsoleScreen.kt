package com.sirena.nina.companion.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.DirectionsCar
import androidx.compose.material.icons.filled.FavoriteBorder
import androidx.compose.material.icons.filled.GridOn
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationRail
import androidx.compose.material3.NavigationRailItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.sirena.nina.companion.CompanionUiState
import com.sirena.nina.companion.CompanionViewModel
import org.json.JSONObject

/**
 * Mirrors [sirena_ui.widgets.sidebar.NAV_ITEMS]: home, drive, vision, map, actions, settings, health.
 * Placeholder content until drive/arms/vision HTTP endpoints are exposed through nina-link.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NinaConsoleScreen(
    vm: CompanionViewModel,
    state: CompanionUiState,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var section by rememberSaveable { mutableStateOf("home") }

    val titles = mapOf(
        "home" to "Nina · Home",
        "drive" to "Nina · Drive",
        "vision" to "Nina · Vision",
        "map" to "Nina · Map (SLAM)",
        "actions" to "Nina · Actions",
        "settings" to "Nina · Settings",
        "health" to "Nina · Health Check",
    )

    Scaffold(
        modifier = modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.surface),
        topBar = {
            TopAppBar(
                title = { Text(titles[section] ?: "Nina") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.primary,
                    titleContentColor = MaterialTheme.colorScheme.onPrimary,
                    navigationIconContentColor = MaterialTheme.colorScheme.onPrimary,
                ),
            )
        },
    ) { padding ->
        Row(
            Modifier
                .padding(padding)
                .fillMaxSize(),
        ) {
            NavigationRail(
                containerColor = MaterialTheme.colorScheme.surfaceVariant,
            ) {
                NavigationRailItem(
                    selected = section == "home",
                    onClick = { section = "home" },
                    icon = { Icon(Icons.Default.Home, "Home") },
                    label = { Text("Home", style = MaterialTheme.typography.labelSmall) },
                )
                NavigationRailItem(
                    selected = section == "drive",
                    onClick = { section = "drive" },
                    icon = { Icon(Icons.Default.DirectionsCar, "Drive") },
                    label = { Text("Drive", style = MaterialTheme.typography.labelSmall) },
                )
                NavigationRailItem(
                    selected = section == "vision",
                    onClick = { section = "vision" },
                    icon = { Icon(Icons.Default.Visibility, "Vision") },
                    label = { Text("Vision", style = MaterialTheme.typography.labelSmall) },
                )
                NavigationRailItem(
                    selected = section == "map",
                    onClick = { section = "map" },
                    icon = { Icon(Icons.Default.GridOn, "Map") },
                    label = { Text("Map", style = MaterialTheme.typography.labelSmall) },
                )
                NavigationRailItem(
                    selected = section == "actions",
                    onClick = { section = "actions" },
                    icon = { Icon(Icons.Default.Menu, "Actions") },
                    label = { Text("Actions", style = MaterialTheme.typography.labelSmall) },
                )
                NavigationRailItem(
                    selected = section == "settings",
                    onClick = { section = "settings" },
                    icon = { Icon(Icons.Default.Settings, "Settings") },
                    label = { Text("Settings", style = MaterialTheme.typography.labelSmall) },
                )
                NavigationRailItem(
                    selected = section == "health",
                    onClick = { section = "health" },
                    icon = { Icon(Icons.Default.FavoriteBorder, "Health") },
                    label = { Text("Health", style = MaterialTheme.typography.labelSmall) },
                )
            }

            Box(
                Modifier
                    .weight(1f)
                    .fillMaxHeight()
                    .background(MaterialTheme.colorScheme.surface),
            ) {
                NinaConsoleSectionPane(section = section, vm = vm, state = state)
            }
        }
    }
}

@Composable
private fun NinaConsoleSectionPane(
    section: String,
    vm: CompanionViewModel,
    state: CompanionUiState,
) {
    val scroll = rememberScrollState()
    val daemonUrl = (state as? CompanionUiState.Ready)?.url
    var caps by remember { mutableStateOf<JSONObject?>(null) }
    var capsErr by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(daemonUrl, section) {
        if (section == "home" && !daemonUrl.isNullOrBlank()) {
            try {
                caps = vm.loadRobotCapabilities()
                capsErr = null
            } catch (e: Exception) {
                capsErr = e.message
                caps = null
            }
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(scroll)
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        PlaceholderHero(section)
        Text(
            sectionPlaceholderSubtitle(section),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        when (section) {
            "home" -> {
                Text("Daemon capabilities", fontWeight = FontWeight.SemiBold)
                when {
                    capsErr != null -> Text(capsErr!!, color = MaterialTheme.colorScheme.error)
                    caps != null -> Text(caps!!.toString(2), style = MaterialTheme.typography.bodySmall)
                    daemonUrl.isNullOrBlank() -> Text("Connect from the main dashboard first.")
                    else -> CircularProgressIndicator(Modifier.padding(8.dp))
                }
            }
            "drive" -> Text(
                "Differential drive / BLDC — wire to NinaService + link daemon (same as sirena_ui drive_screen).",
                style = MaterialTheme.typography.bodySmall,
            )
            "vision" -> Text(
                "Cameras & perception — parity with sirena_ui vision_screen (pipeline controls).",
                style = MaterialTheme.typography.bodySmall,
            )
            "map" -> Text(
                "SLAM / occupancy — parity with sirena_ui map_screen.",
                style = MaterialTheme.typography.bodySmall,
            )
            "actions" -> Text(
                "Animations & audio actions — parity with sirena_ui actions_screen.",
                style = MaterialTheme.typography.bodySmall,
            )
            "settings" -> Text(
                "Robot settings live on the Jetson Sirena UI; use Companion Setup tab for link daemon.",
                style = MaterialTheme.typography.bodySmall,
            )
            "health" -> Text(
                "Subsystem health — parity with sirena_ui health_screen.",
                style = MaterialTheme.typography.bodySmall,
            )
            else -> {}
        }
    }
}

@Composable
private fun PlaceholderHero(section: String) {
    val label = when (section) {
        "home" -> "Home"
        "drive" -> "Drive"
        "vision" -> "Vision"
        "map" -> "Map"
        "actions" -> "Actions"
        "settings" -> "Settings"
        "health" -> "Health"
        else -> "Nina"
    }
    Box(
        Modifier
            .fillMaxWidth()
            .height(160.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(MaterialTheme.colorScheme.surfaceVariant),
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(
                Icons.Default.Build,
                contentDescription = null,
                modifier = Modifier.padding(8.dp),
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                "$label — image placeholder",
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

private fun sectionPlaceholderSubtitle(section: String): String =
    when (section) {
        "home" -> "Overview and robot capabilities from the link daemon."
        "drive" -> "Joystick / autonomy controls will mirror sirena_ui drive_screen."
        "vision" -> "Streams and toggles will mirror sirena_ui vision_screen."
        "map" -> "Mapping UI will mirror sirena_ui map_screen."
        "actions" -> "Macros and playback will mirror sirena_ui actions_screen."
        "settings" -> "Jetson-side settings remain in Sirena Settings on the robot."
        "health" -> "Telemetry cards will mirror sirena_ui health_screen."
        else -> ""
    }
