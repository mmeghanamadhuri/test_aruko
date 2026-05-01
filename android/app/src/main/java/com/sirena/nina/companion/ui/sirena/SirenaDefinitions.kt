package com.sirena.nina.companion.ui.sirena

/**
 * Mirrors [sirena_ui.widgets.sidebar.NAV_ITEMS] — same keys and labels (order matters).
 */
data class NavEntry(val key: String, val label: String)

val SIRENA_NAV_ITEMS: List<NavEntry> =
    listOf(
        NavEntry("home", "Home"),
        NavEntry("drive", "Drive"),
        NavEntry("vision", "Vision"),
        NavEntry("map", "Map"),
        NavEntry("actions", "Actions"),
        NavEntry("settings", "Settings"),
        NavEntry("health", "Health"),
    )

/**
 * Mirrors [sirena_ui.screens.home_screen.QUICK_ACTIONS] —
 * (navKey, label, glyph, blurb). Keys may be deep links `screen:subtab`.
 */
data class QuickAction(val navKey: String, val label: String, val glyph: String, val blurb: String)

val SIRENA_QUICK_ACTIONS: List<QuickAction> =
    listOf(
        QuickAction("actions:playback", "Play action", "\u25B6", "Run a saved motion"),
        QuickAction("actions:record", "Record", "\u25CF", "Capture a new pose"),
        QuickAction("actions:audio", "Audio", "\u266B", "Voice clips"),
        QuickAction("drive", "Drive", "\u2B95", "Manual control"),
        QuickAction("vision", "Vision", "\u25CE", "Camera & faces"),
        QuickAction("map", "Map", "\u25A6", "SLAM & dock"),
        QuickAction("health", "Health", "\u2665", "System checks"),
        QuickAction("settings", "Settings", "\u2699", "Configure"),
    )

/** Mirrors [sirena_ui.screens.actions_screen] subtabs (Playback / Record / Audio). */
val SIRENA_ACTIONS_SUBTAB_LABELS = listOf("Playback", "Record", "Audio")

/** Mirrors [sirena_ui.screens.settings_screen.SETTINGS_CATEGORIES]. */
data class SettingsCategory(val key: String, val label: String, val glyph: String)

val SIRENA_SETTINGS_CATEGORIES: List<SettingsCategory> =
    listOf(
        SettingsCategory("general", "General", "\u2699"),
        SettingsCategory("network", "Network", "\u2706"),
        SettingsCategory("display", "Display", "\u25A1"),
        SettingsCategory("audio", "Audio", "\u266B"),
        SettingsCategory("privacy", "Privacy", "\u26C4"),
        SettingsCategory("autodock", "Autodock", "\u2693"),
        SettingsCategory("voice", "Voice", "\u2693"),
        SettingsCategory("power", "Power", "\u26A1"),
        SettingsCategory("ota", "OTA", "\u21BB"),
    )

/** Mirrors health_screen / status_strip subsystem labels used on Home. */
data class StatusStripItem(val title: String, val value: String)

val SIRENA_HOME_STATUS_OVERVIEW: List<StatusStripItem> =
    listOf(
        StatusStripItem("Bus", "Connecting…"),
        StatusStripItem("Camera", "Not connected"),
        StatusStripItem("Lidar", "Not connected"),
        StatusStripItem("Battery", "n/a"),
        StatusStripItem("Wi‑Fi", "Online"),
    )

/** Labels aligned with [sirena_ui.workers.health_collector] rows (subset for UI scaffold). */
val SIRENA_HEALTH_SUBSYSTEM_LABELS: List<String> =
    listOf(
        "Dynamixel bus",
        "FTDI USB-serial",
        "Serial motor bus",
        "Realsense camera",
        "RPLidar",
        "BLDC navigation",
        "ESP voice module",
    )
