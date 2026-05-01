package com.sirena.nina.companion.ui

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.sirena.nina.companion.ActionRowUi
import com.sirena.nina.companion.CompanionUiState
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.util.NinaLog
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
    val readyState = state as? CompanionUiState.Ready
    val daemonUrl = readyState?.url
    val statusUi = readyState?.status
    var caps by remember { mutableStateOf<JSONObject?>(null) }
    var capsErr by remember { mutableStateOf<String?>(null) }

    val manifestActions by vm.manifestActions.collectAsStateWithLifecycle(emptyList<ActionRowUi>())
    val manifestErr by vm.manifestActionsError.collectAsStateWithLifecycle(null)

    LaunchedEffect(section, daemonUrl) {
        if (section == "actions" && !daemonUrl.isNullOrBlank()) {
            vm.refreshManifestActions()
        }
    }

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
                statusUi = statusUi,
                onNavigate = { key ->
                    NinaLog.tap("SirenaHome", "nav", key)
                    onNavigate(key)
                },
                onSessionClaim = {
                    NinaLog.tap("SirenaHome", "session_claim")
                    vm.sessionClaim { err ->
                        err?.let { NinaLog.warn("session_claim", it) }
                    }
                },
                onSessionRelease = {
                    NinaLog.tap("SirenaHome", "session_release")
                    vm.sessionRelease { err ->
                        err?.let { NinaLog.warn("session_release", it) }
                    }
                },
            )

        "drive" -> SirenaDriveScreen(vm = vm, caps = caps)
        "vision" ->
            SirenaVisionScreen(
                vm = vm,
                daemonUrl = daemonUrl,
                caps = caps,
            )
        "map" -> SirenaMapScreen()
        "actions" ->
            SirenaActionsScreen(
                selectedTab = actionsSubtab,
                onTabSelected = { idx ->
                    NinaLog.tap(
                        "SirenaActions",
                        "subtab",
                        listOf("playback", "record", "audio").getOrElse(idx) { "$idx" },
                    )
                    onActionsSubtabChange(idx)
                },
                manifestActions = manifestActions,
                manifestError = manifestErr,
                onRefreshManifest = {
                    NinaLog.tap("SirenaActions", "refresh_manifest")
                    vm.refreshManifestActions()
                },
                onPlayAction = { vm.playManifestAction(it) },
                vm = vm,
                caps = caps,
            )

        "settings" ->
            SirenaSettingsScreen(
                selectedCategoryKey =
                    settingsCategory.takeIf { key ->
                        SIRENA_SETTINGS_CATEGORIES.any { it.key == key }
                    } ?: "general",
                onCategorySelected = { key ->
                    NinaLog.tap("SirenaSettings", "category", key)
                    onSettingsCategoryChange(key)
                },
                daemonUrl = daemonUrl,
                caps = caps,
                statusUi = readyState?.status,
            )

        "health" ->
            SirenaHealthScreen(
                vm = vm,
                daemonUrl = daemonUrl,
                caps = caps,
                statusUi = readyState?.status,
            )
        else ->
            SirenaHomeScreen(
                caps = caps,
                capsErr = capsErr,
                daemonUrl = daemonUrl,
                statusUi = statusUi,
                onNavigate = { key ->
                    NinaLog.tap("SirenaHome", "nav", key)
                    onNavigate(key)
                },
                onSessionClaim = {
                    vm.sessionClaim { err -> err?.let { NinaLog.warn("session_claim", it) } }
                },
                onSessionRelease = {
                    vm.sessionRelease { err -> err?.let { NinaLog.warn("session_release", it) } }
                },
            )
    }
}
