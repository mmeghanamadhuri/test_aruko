package com.sirena.nina.companion.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
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
    ): JSONObject =
        withContext(Dispatchers.IO) {
            val body =
                JSONObject()
                    .put("action", action)
                    .put("text", text)
                    .put("lang", lang)
                    .put("tld", tld)
                    .put("audio_offset", audioOffsetSec)
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

    suspend fun sessionClaim(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/session/claim", bearer, "{}")
        }

    suspend fun sessionRelease(baseUrl: String, bearer: String?): JSONObject =
        withContext(Dispatchers.IO) {
            post("$baseUrl/v1/session/release", bearer, "{}")
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
        client.newCall(req).execute().use { resp ->
            val body = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) {
                val hint = httpErrorDetail(body, resp.code, resp.message)
                throw LinkApiException(resp.code, hint)
            }
            return if (body.isBlank()) JSONObject() else JSONObject(body)
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
