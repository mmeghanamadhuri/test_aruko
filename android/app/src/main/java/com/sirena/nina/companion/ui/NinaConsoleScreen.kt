package com.sirena.nina.companion.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.outlined.TwoWheeler
import androidx.compose.material.icons.filled.FavoriteBorder
import androidx.compose.material.icons.filled.GridOn
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Sensors
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Visibility
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
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import com.sirena.nina.companion.CompanionUiState
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.util.NinaLog
import com.sirena.nina.companion.ui.sirena.SIRENA_NAV_ITEMS
import com.sirena.nina.companion.ui.sirena.SIRENA_SETTINGS_CATEGORIES
import com.sirena.nina.companion.ui.sirena.SirenaStatusFooter
import org.json.JSONObject

/**
 * Shell mirroring [sirena_ui.main_window] nav — same sections as the desktop robot UI,
 * with deep links (`screen:subtab`) and a charcoal footer status strip.
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
    var actionsSubtab by rememberSaveable { mutableIntStateOf(0) }
    var settingsCategory by rememberSaveable { mutableStateOf("general") }

    fun navigate(navKey: String) {
        NinaLog.tap("NinaConsole", "deep_link", navKey)
        val parts = navKey.split(":", limit = 2)
        val screen = parts[0]
        val sub = parts.getOrNull(1)
        section = screen
        when (screen) {
            "actions" -> {
                actionsSubtab =
                    when (sub) {
                        "playback" -> 0
                        "record" -> 1
                        "audio" -> 2
                        else -> actionsSubtab
                    }
            }
            "settings" -> {
                if (sub != null && SIRENA_SETTINGS_CATEGORIES.any { it.key == sub }) {
                    settingsCategory = sub
                }
            }
        }
    }

    val titles =
        mapOf(
            "home" to "Nina · Home",
            "drive" to "Nina · Drive",
            "vision" to "Nina · Vision",
            "perception" to "Nina · Perception",
            "map" to "Nina · Map (SLAM)",
            "actions" to "Nina · Actions",
            "settings" to "Nina · Settings",
            "health" to "Nina · Health Check",
        )

    val ready = state as? CompanionUiState.Ready
    val jetsonLink by vm.jetsonLink.collectAsStateWithLifecycle()
    var robotCaps by remember { mutableStateOf<JSONObject?>(null) }
    LaunchedEffect(ready?.url) {
        val url = ready?.url
        if (url.isNullOrBlank()) {
            robotCaps = null
            return@LaunchedEffect
        }
        robotCaps =
            try {
                vm.loadRobotCapabilities()
            } catch (_: Exception) {
                null
            }
    }
    val footerRight =
        when {
            ready != null ->
                try {
                    val host = android.net.Uri.parse(ready.url).host
                    if (!host.isNullOrBlank()) host else ready.url
                } catch (_: Exception) {
                    ready.url
                }

            else -> "No daemon link"
        }

    Scaffold(
        modifier =
            modifier
                .fillMaxSize()
                .background(MaterialTheme.colorScheme.surface),
        topBar = {
            TopAppBar(
                title = { Text(titles[section] ?: "Nina") },
                navigationIcon = {
                    IconButton(
                        onClick = {
                            NinaLog.tap("NinaConsole", "top_bar", "back")
                            onBack()
                        },
                    ) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                colors =
                    TopAppBarDefaults.topAppBarColors(
                        containerColor = MaterialTheme.colorScheme.primary,
                        titleContentColor = MaterialTheme.colorScheme.onPrimary,
                        navigationIconContentColor = MaterialTheme.colorScheme.onPrimary,
                    ),
            )
        },
        bottomBar = {
            SirenaStatusFooter(
                busConnected = jetsonLink.isOnline,
                wifiOnline = ready != null,
                batteryOk = false,
                voiceReady = robotCaps?.optBoolean("vision_bridge_enabled") == true,
                rightLabel = footerRight,
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
                SIRENA_NAV_ITEMS.forEach { item ->
                    val icon = navIcon(item.key)
                    NavigationRailItem(
                        selected = section == item.key,
                        onClick = {
                            NinaLog.tap("NinaConsole", "rail", item.key)
                            section = item.key
                        },
                        icon = { Icon(icon, item.label) },
                        label = { Text(item.label, style = MaterialTheme.typography.labelSmall) },
                    )
                }
            }

            Box(
                Modifier
                    .weight(1f)
                    .fillMaxHeight()
                    .background(MaterialTheme.colorScheme.surface),
            ) {
                NinaConsoleSectionContent(
                    section = section,
                    actionsSubtab = actionsSubtab,
                    settingsCategory = settingsCategory,
                    vm = vm,
                    state = state,
                    onNavigate = { navigate(it) },
                    onActionsSubtabChange = { actionsSubtab = it },
                    onSettingsCategoryChange = { settingsCategory = it },
                )
            }
        }
    }
}

@Composable
private fun navIcon(key: String): ImageVector =
    when (key) {
        "home" -> Icons.Default.Home
        "drive" -> Icons.Outlined.TwoWheeler
        "vision" -> Icons.Default.Visibility
        "perception" -> Icons.Default.Sensors
        "map" -> Icons.Default.GridOn
        "actions" -> Icons.Default.Menu
        "settings" -> Icons.Default.Settings
        "health" -> Icons.Default.FavoriteBorder
        else -> Icons.Default.Home
    }
