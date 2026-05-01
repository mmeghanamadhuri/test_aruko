package com.sirena.nina.companion.util

import android.util.Log

/**
 * Diagnostics: **Logcat** (tag [TAG]) and **on-device file** via [NinaFileLogger]
 * (`files/logs/nina_companion.log`) after [NinaFileLogger.install].
 *
 * Filter Logcat: **`NinaCompanion`**. Pull file: `adb shell run-as com.sirena.nina.companion cat files/logs/nina_companion.log`
 */
object NinaLog {
    const val TAG = "NinaCompanion"

    fun tap(screen: String, component: String, detail: String = "") {
        val tail = if (detail.isNotBlank()) " detail=$detail" else ""
        val msg = "tap screen=$screen component=$component$tail"
        Log.i(TAG, msg)
        NinaFileLogger.append("INFO", msg)
    }

    fun api(operation: String, detail: String = "") {
        val tail = if (detail.isNotBlank()) " $detail" else ""
        val msg = "api op=$operation$tail"
        Log.d(TAG, msg)
        NinaFileLogger.append("DEBUG", msg)
    }

    fun warn(where: String, message: String) {
        val msg = "$where: $message"
        Log.w(TAG, msg)
        NinaFileLogger.append("WARN", msg)
    }
}
