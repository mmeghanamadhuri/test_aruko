package com.sirena.nina.companion.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import com.sirena.nina.companion.CompanionUiState
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.ui.sirena.SIRENA_SETTINGS_CATEGORIES
import com.sirena.nina.companion.ui.sirena.SirenaActionsScreen
import com.sirena.nina.companion.ui.sirena.SirenaDriveScreen
import com.sirena.nina.companion.ui.sirena.SirenaHealthScreen
import com.sirena.nina.companion.ui.sirena.SirenaHomeScreen
import com.sirena.nina.companion.ui.sirena.SirenaMapScreen
import com.sirena.nina.companion.ui.sirena.SirenaSettingsScreen
import com.sirena.nina.companion.ui.sirena.SirenaVisionScreen
import org.json.JSONObject

/**
 * Routes console sections to sirena_ui–parity layouts ([sirena_ui] desktop app).
 */
@Composable
fun NinaConsoleSectionContent(
    section: String,
    actionsSubtab: Int,
    settingsCategory: String,
    vm: CompanionViewModel,
    state: CompanionUiState,
    onNavigate: (String) -> Unit,
    onActionsSubtabChange: (Int) -> Unit,
    onSettingsCategoryChange: (String) -> Unit,
) {
    val daemonUrl = (state as? CompanionUiState.Ready)?.url
    var caps by remember { mutableStateOf<JSONObject?>(null) }
    var capsErr by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(daemonUrl) {
        if (!daemonUrl.isNullOrBlank()) {
            try {
                caps = vm.loadRobotCapabilities()
                capsErr = null
            } catch (e: Exception) {
                capsErr = e.message
                caps = null
            }
        }
    }

    when (section) {
        "home" ->
            SirenaHomeScreen(
                caps = caps,
                capsErr = capsErr,
                daemonUrl = daemonUrl,
                onNavigate = onNavigate,
            )

        "drive" -> SirenaDriveScreen(vm = vm, caps = caps)
        "vision" -> SirenaVisionScreen()
        "map" -> SirenaMapScreen()
        "actions" ->
            SirenaActionsScreen(
                selectedTab = actionsSubtab,
                onTabSelected = onActionsSubtabChange,
            )

        "settings" ->
            SirenaSettingsScreen(
                selectedCategoryKey =
                    settingsCategory.takeIf { key ->
                        SIRENA_SETTINGS_CATEGORIES.any { it.key == key }
                    } ?: "general",
                onCategorySelected = onSettingsCategoryChange,
            )

        "health" -> SirenaHealthScreen()
        else ->
            SirenaHomeScreen(
                caps = caps,
                capsErr = capsErr,
                daemonUrl = daemonUrl,
                onNavigate = onNavigate,
            )
    }
}
