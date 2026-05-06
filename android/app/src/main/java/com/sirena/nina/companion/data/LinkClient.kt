package com.sirena.nina.companion.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import com.sirena.nina.companion.util.NinaLog
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

/** HTTP client for the Jetson nina-link daemon (matches `nina/link_daemon/api.py`). */
class LinkClient {

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(45, TimeUnit.SECONDS)
        .writeTimeout(45, TimeUnit.SECONDS)
        .build()

    private val jsonMedia = "application/json; charset=utf-8".toMediaType()

    suspend fun health(baseUrl: String): JSONObject = withContext(Dispatchers.IO) {
        get("$baseUrl/health")
    }

    suspend fun status(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/status", bearer)
        }

    suspend fun setMode(baseUrl: String, bearer: String?, mode: String): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/mode",
                bearer,
                JSONObject().put("mode", mode).toString(),
            )
        }

    suspend fun saveHomeWifi(
        baseUrl: String,
        bearer: String?,
        ssid: String,
        password: String,
    ): JSONObject = withContext(Dispatchers.IO) {
        post(
            "$baseUrl/v1/wifi/home-credentials",
            bearer,
            JSONObject()
                .put("ssid", ssid)
                .put("password", password)
                .toString(),
        )
    }

    suspend fun connectHome(baseUrl: String, bearer: String?, ssid: String?): JSONObject =
        withContext(Dispatchers.IO) {
            val url = buildString {
                append("$baseUrl/v1/wifi/connect-home")
                if (!ssid.isNullOrBlank()) append("?ssid=${java.net.URLEncoder.encode(ssid, Charsets.UTF_8.name())}")
            }
            post(url, bearer, "{}")
        }

    suspend fun startAp(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/wifi/start-ap", bearer, "{}")
        }

    suspend fun deleteSaved(baseUrl: String, bearer: String?, profileId: String): JSONObject =
        withContext(Dispatchers.IO) {
            delete("$baseUrl/v1/wifi/saved/${java.net.URLEncoder.encode(profileId, Charsets.UTF_8.name())}", bearer)
        }

    suspend fun pair(baseUrl: String, pin: String): JSONObject = withContext(Dispatchers.IO) {
        post(
            "$baseUrl/v1/pair",
            null,
            JSONObject().put("pin", pin).toString(),
        )
    }

    suspend fun capabilities(baseUrl: String): JSONObject = withContext(Dispatchers.IO) {
        get("$baseUrl/v1/robot/capabilities")
    }

    /** Aggregated subsystem health (lidar, BLDC, vision, depth, …) for the Health screen. */
    suspend fun robotHealth(baseUrl: String): JSONObject = withContext(Dispatchers.IO) {
        get("$baseUrl/v1/robot/health")
    }

    /** Save current SLAM grid as PGM under ``nina/data/maps/`` on the Jetson. */
    suspend fun slamSave(
        baseUrl: String,
        bearer: String?,
        filename: String,
    ): JSONObject = withContext(Dispatchers.IO) {
        post(
            "$baseUrl/v1/slam/save",
            bearer,
            JSONObject().put("filename", filename).toString(),
        )
    }

    suspend fun robotDriveMomentary(
        baseUrl: String,
        bearer: String?,
        direction: String,
        durationMs: Int,
        speedPercent: Int? = null,
    ): JSONObject = withContext(Dispatchers.IO) {
        val json = JSONObject().put("direction", direction).put("duration_ms", durationMs)
        if (speedPercent != null) json.put("speed_percent", speedPercent)
        post("$baseUrl/v1/robot/drive", bearer, json.toString())
    }

    suspend fun robotEmergencyStop(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/robot/emergency-stop", bearer, "{}")
        }

    /** Ask the Jetson host to power off (requires passwordless sudo on the robot — see nina-link docs). */
    suspend fun systemPoweroff(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/system/poweroff", bearer, "{}")
        }

    /** BLDC hardware readiness (lazy NavigationManager probe; matches desktop Drive pill). */
    suspend fun robotDriveStatus(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/robot/drive/status")
        }

    /** Per-wheel polarity flip (matches Qt Drive Flip L/R). */
    suspend fun robotDriveInvert(
        baseUrl: String,
        bearer: String?,
        left: Boolean?,
        right: Boolean?,
    ): JSONObject = withContext(Dispatchers.IO) {
        val json = JSONObject()
        if (left != null) json.put("left", left)
        if (right != null) json.put("right", right)
        post("$baseUrl/v1/robot/drive/invert", bearer, json.toString())
    }

    /** Manifest-backed action list from the Jetson (`nina/actions/manifest.json`). */
    suspend fun listActions(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/actions")
        }

    /** Runs a named action on the Jetson when `NINA_LINK_ENABLE_ACTION_BRIDGE=1`. */
    suspend fun playAction(baseUrl: String, bearer: String?, actionName: String): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/actions/play",
                bearer,
                JSONObject().put("action", actionName).toString(),
            )
        }

    suspend fun listRecordings(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) { get("$baseUrl/v1/actions/recordings") }

    suspend fun recordStatus(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) { get("$baseUrl/v1/actions/record/status") }

    suspend fun recordStart(
        baseUrl: String,
        bearer: String?,
        name: String,
        seconds: Double,
        hz: Double,
        countdown: Double,
        holdAfter: Boolean,
        register: Boolean,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            val body =
                JSONObject()
                    .put("name", name)
                    .put("seconds", seconds)
                    .put("hz", hz)
                    .put("countdown", countdown)
                    .put("hold_after", holdAfter)
                    .put("register", register)
            post("$baseUrl/v1/actions/record/start", bearer, body.toString())
        }

    suspend fun recordStop(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/actions/record/stop", bearer, "{}")
        }

    /** Jetson manifest audio editor (`GET /v1/actions/audio/info`). */
    suspend fun actionAudioInfo(baseUrl: String, action: String): JSONObject =
        withContext(Dispatchers.IO) {
            val enc = java.net.URLEncoder.encode(action, Charsets.UTF_8.name())
            get("$baseUrl/v1/actions/audio/info?action=$enc")
        }

    suspend fun actionAudioOffset(
        baseUrl: String,
        bearer: String?,
        action: String,
        audioOffsetSec: Double,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            val body =
                JSONObject()
                    .put("action", action)
                    .put("audio_offset", audioOffsetSec)
            post("$baseUrl/v1/actions/audio/offset", bearer, body.toString())
        }

    suspend fun actionAudioClear(baseUrl: String, bearer: String?, action: String): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/actions/audio/clear",
                bearer,
                JSONObject().put("action", action).toString(),
            )
        }

    suspend fun actionAudioGenerate(
        baseUrl: String,
        bearer: String?,
        action: String,
        text: String,
        lang: String,
        tld: String,
        audioOffsetSec: Double,
        slow: Boolean = false,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            val body =
                JSONObject()
                    .put("action", action)
                    .put("text", text)
                    .put("lang", lang)
                    .put("tld", tld)
                    .put("audio_offset", audioOffsetSec)
                    .put("slow", slow)
            post("$baseUrl/v1/actions/audio/generate", bearer, body.toString())
        }

    /** Remove manifest entry and optionally delete files (`POST /v1/actions/delete`). */
    suspend fun deleteManifestAction(
        baseUrl: String,
        bearer: String?,
        action: String,
        deleteRecording: Boolean = true,
        deleteAudio: Boolean = false,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            val body =
                JSONObject()
                    .put("action", action)
                    .put("delete_recording", deleteRecording)
                    .put("delete_audio", deleteAudio)
            post("$baseUrl/v1/actions/delete", bearer, body.toString())
        }

    suspend fun visionStatus(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) { get("$baseUrl/v1/vision/status") }

    suspend fun visionOptions(
        baseUrl: String,
        bearer: String?,
        face: Boolean?,
        objects: Boolean?,
        objectConfidence: Double?,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            val o = JSONObject()
            if (face != null) o.put("face", face)
            if (objects != null) o.put("objects", objects)
            if (objectConfidence != null) o.put("object_confidence", objectConfidence)
            post("$baseUrl/v1/vision/options", bearer, o.toString())
        }

    suspend fun visionOpen(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/vision/open", bearer, "{}")
        }

    suspend fun visionStop(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/vision/stop", bearer, "{}")
        }

    /** Queue face enrollment on the Jetson (same 8-sample flow as Sirena UI). */
    suspend fun visionEnroll(
        baseUrl: String,
        bearer: String?,
        name: String,
        targetSamples: Int = 8,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/vision/enroll",
                bearer,
                JSONObject()
                    .put("name", name)
                    .put("target_samples", targetSamples)
                    .toString(),
            )
        }

    suspend fun visionEnrollStatus(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/vision/enroll/status")
        }

    /** gTTS + play on robot for current object labels (matches desktop “Play objects”). */
    suspend fun visionAnnounce(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/vision/announce", bearer, "{}")
        }

    suspend fun visionAnnounceStatus(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/vision/announce/status")
        }

    suspend fun visionDetections(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/vision/detections")
        }

    suspend fun sessionClaim(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/session/claim", bearer, "{}")
        }

    suspend fun sessionRelease(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/session/release", bearer, "{}")
        }

    suspend fun slamStatus(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/slam/status")
        }

    suspend fun slamSnapshot(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/slam/snapshot")
        }

    /**
     * Raw occupancy grid (`application/octet-stream`) plus dimensions from response headers.
     */
    suspend fun slamOccupancyGrid(baseUrl: String): SlamOccupancyGrid? =
        withContext(Dispatchers.IO) {
            val url = "$baseUrl/v1/slam/occupancy".toHttpUrlOrNull() ?: return@withContext null
            val req = Request.Builder().url(url).get().build()
            client.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext null
                val w = resp.header("X-Slam-Width")?.toIntOrNull() ?: return@withContext null
                val h = resp.header("X-Slam-Height")?.toIntOrNull() ?: return@withContext null
                val bytes = resp.body?.bytes() ?: return@withContext null
                if (bytes.size < w * h) return@withContext null
                SlamOccupancyGrid(bytes, w, h)
            }
        }

    suspend fun depthStatus(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/depth/status")
        }

    suspend fun autonomyStatus(baseUrl: String): JSONObject =
        withContext(Dispatchers.IO) {
            get("$baseUrl/v1/autonomy/status")
        }

    suspend fun setAutonomyEnabled(
        baseUrl: String,
        bearer: String?,
        enabled: Boolean,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/autonomy/enabled",
                bearer,
                JSONObject().put("enabled", enabled).toString(),
            )
        }

    /**
     * POST /v1/autonomy/goal — arm the goto pilot to drive to (x, y) mm.
     * World frame = SLAM map frame: origin at map centre, +x right, +y forward.
     * Caller converts a tap on the occupancy bitmap to mm using the snapshot's
     * scale + width/height before invoking this.
     */
    suspend fun setAutonomyGoal(
        baseUrl: String,
        bearer: String?,
        xMm: Double,
        yMm: Double,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            post(
                "$baseUrl/v1/autonomy/goal",
                bearer,
                JSONObject().put("x_mm", xMm).put("y_mm", yMm).toString(),
            )
        }

    /** DELETE /v1/autonomy/goal — cancel an in-flight goto. */
    suspend fun clearAutonomyGoal(
        baseUrl: String,
        bearer: String?,
    ): JSONObject =
        withContext(Dispatchers.IO) {
            delete("$baseUrl/v1/autonomy/goal", bearer)
        }

    private fun get(url: String, bearer: String? = null): JSONObject {
        val req = Request.Builder()
            .url(url)
            .header("Accept", "application/json")
            .apply { if (!bearer.isNullOrBlank()) header("Authorization", "Bearer $bearer") }
            .get()
            .build()
        return execute(req)
    }

    private fun post(url: String, bearer: String?, jsonBody: String): JSONObject {
        val body = jsonBody.toRequestBody(jsonMedia)
        val req = Request.Builder()
            .url(url)
            .header("Accept", "application/json")
            .apply { if (!bearer.isNullOrBlank()) header("Authorization", "Bearer $bearer") }
            .post(body)
            .build()
        return execute(req)
    }

    private fun delete(url: String, bearer: String?): JSONObject {
        val req = Request.Builder()
            .url(url)
            .header("Accept", "application/json")
            .apply { if (!bearer.isNullOrBlank()) header("Authorization", "Bearer $bearer") }
            .delete()
            .build()
        return execute(req)
    }

    private fun execute(req: Request): JSONObject {
        try {
            client.newCall(req).execute().use { resp ->
                val body = resp.body?.string().orEmpty()
                if (!resp.isSuccessful) {
                    val hint = httpErrorDetail(body, resp.code, resp.message)
                    val path = req.url.encodedPath
                    val quietSlamSnapshot =
                        resp.code == 404 &&
                            req.method == "GET" &&
                            path.endsWith("/v1/slam/snapshot")
                    if (!quietSlamSnapshot) {
                        NinaLog.warn(
                            "LinkClient",
                            "${req.method} ${req.url} -> ${resp.code} $hint",
                        )
                    }
                    throw LinkApiException(resp.code, hint)
                }
                return if (body.isBlank()) JSONObject() else JSONObject(body)
            }
        } catch (e: IOException) {
            NinaLog.warn("LinkClient", "${req.method} ${req.url} -> ${e.message}")
            throw e
        }
    }

    /**
     * FastAPI often returns `detail` as a string, a list of validation objects, or nested JSON.
     * [JSONObject.optString] turns JSON null into the literal `"null"` — callers must use [jsonCleanString] instead.
     */
    private fun httpErrorDetail(body: String, httpCode: Int, httpMessage: String?): String {
        val fallback =
            httpMessage?.takeIf { it.isNotBlank() && !it.equals("null", ignoreCase = true) }
                ?: "HTTP $httpCode"
        if (body.isBlank()) return fallback
        return try {
            val j = JSONObject(body)
            when {
                j.has("detail") && !j.isNull("detail") -> {
                    when (val d = j.get("detail")) {
                        is String -> d.trim().ifBlank { fallback }
                        is JSONArray -> {
                            val parts = mutableListOf<String>()
                            for (i in 0 until d.length()) {
                                val item = d.optJSONObject(i)
                                val msg = item?.optString("msg")?.trim().orEmpty()
                                if (msg.isNotEmpty()) parts.add(msg)
                            }
                            parts.joinToString("; ").ifBlank { d.toString() }
                        }

                        else -> d.toString().trim().ifBlank { fallback }
                    }
                }

                j.has("message") && !j.isNull("message") ->
                    j.optString("message").trim().ifBlank { fallback }

                else -> j.toString().trim().ifBlank { fallback }
            }
        } catch (_: Exception) {
            body.trim().ifBlank { fallback }
        }
    }
}

/** JSON field safe for optional strings (never returns literal `"null"`). */
fun JSONObject.jsonCleanString(key: String): String? {
    if (!has(key) || isNull(key)) return null
    val s = optString(key).trim()
    if (s.isEmpty() || s.equals("null", ignoreCase = true)) return null
    return s
}

class LinkApiException(val code: Int, message: String) : Exception(message)

/** Raw SLAM occupancy grid bytes (``width * height`` uint8 cells). */
data class SlamOccupancyGrid(val bytes: ByteArray, val width: Int, val height: Int) {
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (javaClass != other?.javaClass) return false
        other as SlamOccupancyGrid
        if (width != other.width || height != other.height) return false
        if (!bytes.contentEquals(other.bytes)) return false
        return true
    }

    override fun hashCode(): Int {
        var result = width
        result = 31 * result + height
        result = 31 * result + bytes.contentHashCode()
        return result
    }
}
