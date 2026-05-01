package com.sirena.nina.companion.util

import android.content.Context
import java.io.File
import java.io.FileOutputStream
import java.io.OutputStreamWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.Executors

/**
 * Persists companion diagnostics into **app-private** storage (no permission needed):
 *
 * **`/data/data/<package>/files/logs/nina_companion.log`**
 *
 * Same events as [NinaLog] Logcat lines. Rotates to `nina_companion.old.log` past [MAX_BYTES].
 */
object NinaFileLogger {

    const val LOG_SUBDIR = "logs"
    const val ACTIVE_LOG_NAME = "nina_companion.log"
    const val ROTATED_LOG_NAME = "nina_companion.old.log"

    private const val MAX_BYTES = 2 * 1024 * 1024

    private val isoFmt =
        SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }

    private val executor = Executors.newSingleThreadExecutor { r ->
        Thread(r, "NinaFileLogger").apply { isDaemon = true }
    }

    @Volatile
    private var installed = false

    private var filesDir: File? = null

    fun install(context: Context) {
        synchronized(this) {
            if (installed) return
            filesDir = context.applicationContext.filesDir
            logDirectory(context).mkdirs()
            writeLineLocked("INFO", "session_start pkg=${context.applicationContext.packageName}")
            installed = true
        }
    }

    fun isInstalled(): Boolean = installed

    fun logDirectory(context: Context): File =
        File(context.applicationContext.filesDir, LOG_SUBDIR)

    fun activeLogFile(context: Context): File =
        File(logDirectory(context), ACTIVE_LOG_NAME)

    fun append(level: String, message: String) {
        if (!installed || filesDir == null) return
        val line = message.replace("\r\n", " ").replace("\n", " ").trim()
        executor.execute {
            try {
                writeLineLocked(level, line)
            } catch (_: Exception) {
            }
        }
    }

    private fun writeLineLocked(level: String, message: String) {
        val base = filesDir ?: return
        val dir = File(base, LOG_SUBDIR)
        dir.mkdirs()
        val file = File(dir, ACTIVE_LOG_NAME)
        rotateIfNeeded(file)
        OutputStreamWriter(FileOutputStream(file, true), Charsets.UTF_8).use { w ->
            w.append(isoFmt.format(Date()))
            w.append(' ')
            w.append(level)
            w.append(' ')
            w.append(message)
            w.append('\n')
        }
    }

    private fun rotateIfNeeded(file: File) {
        if (!file.exists() || file.length() < MAX_BYTES) return
        val parent = file.parentFile ?: return
        val rotated = File(parent, ROTATED_LOG_NAME)
        try {
            if (rotated.exists()) rotated.delete()
            file.renameTo(rotated)
        } catch (_: Exception) {
        }
    }
}
