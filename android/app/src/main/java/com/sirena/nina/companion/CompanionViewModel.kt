package com.sirena.nina.companion

import android.app.Application
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import kotlin.jvm.Volatile
import androidx.lifecycle.viewModelScope
import com.sirena.nina.companion.data.LinkApiException
import com.sirena.nina.companion.data.LinkClient
import com.sirena.nina.companion.data.SlamOccupancyGrid
import com.sirena.nina.companion.data.jsonCleanString
import com.sirena.nina.companion.data.Prefs
import com.sirena.nina.companion.network.DaemonUrlResolver
import com.sirena.nina.companion.network.LanDaemonScanner
import com.sirena.nina.companion.util.NinaLog
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject

data class StatusUi(
    val wifiRole: String,
    val ipv4: String?,
    val userMode: String,
    val bootWaitRemainingSec: Int,
    val clientSeen: Boolean,
    val lastError: String?,
    val savedNetworks: List<SavedNetUi>,
    val apSsid: String?,
    val activeStaSsid: String?,
    val activeStaProfile: String?,
    /** Jetson has issued a session token (fleet / pairing). */
    val paired: Boolean,
)

data class SavedNetUi(
    val id: String,
    val uuid: String,
    val ssid: String,
    val nmAutoconnect: Boolean,
)

/** Fast HTTP liveness to saved daemon URL (independent of full status refresh). */
data class JetsonLinkState(
    val isOnline: Boolean = false,
    val lastError: String? = null,
)

/** One row from Jetson `GET /v1/actions` (manifest). */
data class ActionRowUi(
    val name: String,
    val file: String?,
    val audio: String?,
    val audioOffsetSec: Double?,
)

sealed interface CompanionUiState {
    data object Loading : CompanionUiState
    data class Ready(val url: String, val status: StatusUi?, val message: String?) : CompanionUiState
    data class Error(val text: String) : CompanionUiState
}

class CompanionViewModel(app: Application) : AndroidViewModel(app) {

    private val prefs = Prefs(app)
    private val client = LinkClient()

    private val appCtx get() = getApplication<Application>()

    /**
     * True after a successful session claim (kiosk stopped for nina-link).
     * Used so we release on leaving the Nina console or process death.
     */
    @Volatile
    private var robotConsoleSessionActive: Boolean = false

    /** Persisted daemon URL (normalized). */
    val savedDaemonUrl: Flow<String> = prefs.baseUrl

    private val _gatewayHint = MutableStateFlow<String?>(null)
    val gatewayHint: StateFlow<String?> = _gatewayHint.asStateFlow()

    private val _manifestActions = MutableStateFlow<List<ActionRowUi>>(emptyList())
    val manifestActions: StateFlow<List<ActionRowUi>> = _manifestActions.asStateFlow()

    private val _manifestActionsError = MutableStateFlow<String?>(null)
    val manifestActionsError: StateFlow<String?> = _manifestActionsError.asStateFlow()

    private val _state = MutableStateFlow<CompanionUiState>(CompanionUiState.Loading)
    val state: StateFlow<CompanionUiState> = _state.asStateFlow()

    private val _jetsonLink = MutableStateFlow(JetsonLinkState())
    val jetsonLink: StateFlow<JetsonLinkState> = _jetsonLink.asStateFlow()

    init {
        refreshStatus()
        viewModelScope.launch {
            while (isActive) {
                val url =
                    try {
                        prefs.baseUrl.first()
                    } catch (_: Exception) {
                        ""
                    }
                if (url.isBlank()) {
                    _jetsonLink.value = JetsonLinkState(false, null)
                    delay(3000)
                    continue
                }
                try {
                    client.health(url)
                    _jetsonLink.value = JetsonLinkState(true, null)
                } catch (e: Exception) {
                    val msg = e.message?.trim()?.take(120)
                    _jetsonLink.value = JetsonLinkState(false, msg)
                }
                delay(2500)
            }
        }
    }

    fun refreshStatus() {
        viewModelScope.launch {
            try {
                val (url, statusUi) = resolveAndFetchStatus()
                val gw = DaemonUrlResolver.gatewayIpv4(appCtx)
                val myIp = DaemonUrlResolver.deviceIpv4(appCtx)
                _gatewayHint.value = buildDiscoveryHint(myIp, gw)
                _state.value = CompanionUiState.Ready(url, statusUi, null)
            } catch (e: LinkApiException) {
                NinaLog.warn("refreshStatus", friendlyHttp(e))
                _state.update {
                    CompanionUiState.Error(friendlyHttp(e))
                }
            } catch (e: Exception) {
                NinaLog.warn("refreshStatus", e.message ?: "unknown")
                _state.value = CompanionUiState.Error(
                    e.message ?: "Could not reach Nina Link daemon. Check Wi‑Fi.",
                )
            }
        }
    }

    /**
     * Try several URLs: hotspot gateway first when it looks like an NM AP, then saved prefs,
     * then common Jetson defaults — avoids using the tablet's own IP by mistake.
     */
    private suspend fun resolveAndFetchStatus(): Pair<String, StatusUi> {
        val bearer = prefs.bearerToken.first()
        val savedNorm = Prefs.normalizeBaseUrl(prefs.baseUrl.first())
        val myIp = DaemonUrlResolver.deviceIpv4(appCtx)
        val homeLan = DaemonUrlResolver.isTypicalHomeLanClient(myIp)
        var lastError: Exception? = null
        val tried = mutableSetOf<String>()

        suspend fun attempt(url: String): Pair<String, StatusUi>? {
            try {
                assertUrlNotTabletOwnIp(url)
                client.health(url)
                val st = client.status(url, bearer)
                prefs.setBaseUrl(url)
                return url to parseStatus(st)
            } catch (e: IllegalArgumentException) {
                lastError = e
                return null
            } catch (e: Exception) {
                lastError = e
                return null
            }
        }

        suspend fun tryNormalized(raw: String): Pair<String, StatusUi>? {
            val n = Prefs.normalizeBaseUrl(raw)
            if (n in tried) return null
            tried.add(n)
            return attempt(n)
        }

        for (raw in buildCandidateUrls(savedNorm)) {
            tryNormalized(raw)?.let { return it }
        }

        // Same Wi‑Fi as the Jetson but saved URL / gateway guesses failed — scan the /24 for :8787.
        if (homeLan) {
            for (base in LanDaemonScanner.scanIpv4Subnet(myIp)) {
                tryNormalized(base)?.let { return it }
            }
        }

        throw lastError ?: IllegalStateException("Could not reach Nina Link.")
    }

    private fun buildDiscoveryHint(myIp: String?, gw: String?): String {
        return when {
            DaemonUrlResolver.isTypicalHomeLanClient(myIp) ->
                "Home Wi‑Fi: the router (${gw ?: "gateway"}) is not the robot. " +
                    "This app tries your saved URL first, then scans this subnet for port 8787. " +
                    "You can still set the Jetson address manually under Setup."
            gw != null ->
                "Jetson AP gateway (if any): http://$gw:8787 — home routers are never used as the daemon host."
            else ->
                "Open Setup and enter the Jetson link-daemon URL if discovery fails."
        }
    }

    /**
     * Fast candidates only — no full-subnet scan (scan runs in [resolveAndFetchStatus] on failure).
     * Never treats the home LAN default gateway as the Jetson (that caused connects to e.g. 192.168.1.1).
     */
    private fun buildCandidateUrls(savedNorm: String): List<String> {
        val myIp = DaemonUrlResolver.deviceIpv4(appCtx)
        val gw = DaemonUrlResolver.gatewayIpv4(appCtx)
        val hotspot = DaemonUrlResolver.isNinaHotspotClient(myIp)

        val candidates = mutableListOf<String>()

        fun offer(raw: String) {
            val n = Prefs.normalizeBaseUrl(raw)
            val host = Uri.parse(n).host ?: return
            if (host.equals(myIp, ignoreCase = true)) return
            if (n !in candidates) candidates.add(n)
        }

        offer(savedNorm)

        // Only Nina hotspot / USB-tether gateways host nina-link — never a typical home router.
        if (gw != null &&
            DaemonUrlResolver.isLikelyJetsonApGateway(gw) &&
            !gw.equals(myIp, ignoreCase = true)
        ) {
            offer("http://$gw:8787")
        }

        DaemonUrlResolver.heuristicGatewayForDeviceIp(myIp)?.let { offer("http://$it:8787") }

        if (hotspot) {
            offer("http://10.42.0.1:8787")
            offer("http://192.168.4.1:8787")
        } else {
            if (myIp?.startsWith("10.42.") == true) offer("http://10.42.0.1:8787")
            if (myIp?.startsWith("192.168.4.") == true) offer("http://192.168.4.1:8787")
        }

        return candidates
    }

    private fun assertUrlNotTabletOwnIp(url: String) {
        val host = Uri.parse(url).host ?: return
        val my = DaemonUrlResolver.deviceIpv4(appCtx) ?: return
        if (host.equals(my, ignoreCase = true)) {
            throw IllegalArgumentException(
                "That address ($host) is this tablet, not the Jetson. " +
                    "Enter the robot's LAN IP from your router's client list, or connect via Nina AP " +
                    "(e.g. http://10.42.0.1:8787).",
            )
        }
    }

    fun ping(urlOverride: String? = null) {
        viewModelScope.launch {
            try {
                val raw = urlOverride?.trim() ?: prefs.baseUrl.first()
                val url = Prefs.normalizeBaseUrl(raw)
                assertUrlNotTabletOwnIp(url)
                client.health(url)
                prefs.setBaseUrl(url)
                refreshStatus()
            } catch (e: Exception) {
                _state.value = CompanionUiState.Error(e.message ?: "Ping failed")
            }
        }
    }

    fun saveBaseUrl(url: String) {
        viewModelScope.launch {
            try {
                assertUrlNotTabletOwnIp(Prefs.normalizeBaseUrl(url))
                prefs.setBaseUrl(url)
                refreshStatus()
            } catch (e: IllegalArgumentException) {
                _state.value = CompanionUiState.Error(e.message ?: "Invalid URL")
            } catch (e: Exception) {
                _state.value = CompanionUiState.Error(e.message ?: "Save failed")
            }
        }
    }

    fun saveBearer(token: String?) {
        viewModelScope.launch {
            prefs.setBearerToken(token)
            refreshStatus()
        }
    }

    fun setMode(mode: String) {
        viewModelScope.launch {
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                client.setMode(url, bearer, mode)
                refreshStatus()
            } catch (e: Exception) {
                _state.value = CompanionUiState.Error(e.message ?: "Mode failed")
            }
        }
    }

    fun saveHomeAndOptionallyConnect(ssid: String, password: String, connect: Boolean) {
        viewModelScope.launch {
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                client.saveHomeWifi(url, bearer, ssid, password)
                if (connect) {
                    client.connectHome(url, bearer, null)
                }
                refreshStatus()
            } catch (e: Exception) {
                _state.value = CompanionUiState.Error(e.message ?: "Save Wi‑Fi failed")
            }
        }
    }

    fun connectJetsonHome(ssid: String?) {
        viewModelScope.launch {
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                client.connectHome(url, bearer, ssid)
                refreshStatus()
            } catch (e: Exception) {
                _state.value = CompanionUiState.Error(e.message ?: "Connect failed — check password on Jetson.")
            }
        }
    }

    fun startApOnJetson() {
        viewModelScope.launch {
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                client.startAp(url, bearer)
                refreshStatus()
            } catch (e: Exception) {
                _state.value = CompanionUiState.Error(e.message ?: "Could not start AP on Jetson")
            }
        }
    }

    fun deleteProfile(profileId: String) {
        viewModelScope.launch {
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                client.deleteSaved(url, bearer, profileId)
                refreshStatus()
            } catch (e: Exception) {
                _state.value = CompanionUiState.Error(e.message ?: "Delete failed")
            }
        }
    }

    fun pair(pin: String, onToken: (String) -> Unit) {
        viewModelScope.launch {
            try {
                val url = prefs.baseUrl.first()
                val body = client.pair(url, pin)
                val token = body.optString("token", "")
                if (token.isNotBlank()) {
                    prefs.setBearerToken(token)
                    onToken(token)
                }
                refreshStatus()
            } catch (e: Exception) {
                _state.value = CompanionUiState.Error(e.message ?: "Pairing failed")
            }
        }
    }

    suspend fun loadRobotCapabilities(): JSONObject {
        val url = prefs.baseUrl.first()
        return client.capabilities(url)
    }

    fun refreshManifestActions() {
        viewModelScope.launch {
            NinaLog.api("GET", "/v1/actions")
            try {
                val url = prefs.baseUrl.first()
                val j = client.listActions(url)
                val arr = j.optJSONArray("actions") ?: JSONArray()
                val list = mutableListOf<ActionRowUi>()
                for (i in 0 until arr.length()) {
                    val o = arr.getJSONObject(i)
                    val off =
                        when {
                            !o.has("audio_offset") -> null
                            o.isNull("audio_offset") -> null
                            else -> o.optDouble("audio_offset").takeUnless { it.isNaN() }
                        }
                    list.add(
                        ActionRowUi(
                            name = o.optString("name"),
                            file = o.optString("file").takeIf { it.isNotBlank() },
                            audio = o.optString("audio").takeIf { it.isNotBlank() },
                            audioOffsetSec = off,
                        ),
                    )
                }
                _manifestActions.value = list.sortedBy { it.name.lowercase() }
                _manifestActionsError.value = null
                NinaLog.api("manifest_actions", "ok count=${list.size}")
            } catch (e: Exception) {
                NinaLog.warn("manifest_actions", e.message ?: "failed")
                _manifestActionsError.value = e.message
                _manifestActions.value = emptyList()
            }
        }
    }

    fun playManifestAction(name: String) {
        NinaLog.tap("Actions", "play_motion", name)
        viewModelScope.launch {
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                NinaLog.api("POST", "/v1/actions/play action=$name")
                client.playAction(url, bearer, name)
                _manifestActionsError.value = null
            } catch (e: Exception) {
                NinaLog.warn("play_action", e.message ?: "failed")
                _manifestActionsError.value = e.message ?: "Play failed"
            }
        }
    }

    /** Momentary drive pulse — requires Jetson `NINA_LINK_ENABLE_ROBOT_BRIDGE=1`. */
    suspend fun robotDriveMomentary(
        direction: String,
        durationMs: Int = 280,
        speedPercent: Int? = null,
    ): JSONObject {
        NinaLog.tap("Drive", "momentary", "$direction ${durationMs}ms speed=$speedPercent")
        val url = prefs.baseUrl.first()
        val bearer = prefs.bearerToken.first()
        return client.robotDriveMomentary(url, bearer, direction, durationMs, speedPercent)
    }

    suspend fun fetchRobotDriveStatus(): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            client.robotDriveStatus(url)
        } catch (_: Exception) {
            null
        }

    suspend fun postRobotDriveInvert(left: Boolean?, right: Boolean?): JSONObject? {
        if (left == null && right == null) return null
        return try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            NinaLog.tap("Drive", "invert", "L=$left R=$right")
            client.robotDriveInvert(url, bearer, left, right)
        } catch (_: Exception) {
            null
        }
    }

    suspend fun robotEmergencyStop(): JSONObject {
        NinaLog.tap("Drive", "emergency_stop", "")
        val url = prefs.baseUrl.first()
        val bearer = prefs.bearerToken.first()
        return client.robotEmergencyStop(url, bearer)
    }

    fun requestJetsonShutdown(onResult: (String?) -> Unit) {
        NinaLog.tap("System", "jetson_poweroff", "")
        viewModelScope.launch {
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                client.systemPoweroff(url, bearer)
                onResult(null)
            } catch (e: Exception) {
                onResult(e.message ?: "Poweroff request failed")
            }
        }
    }

    private fun parseStatus(j: JSONObject): StatusUi {
        val saved = mutableListOf<SavedNetUi>()
        val arr = j.optJSONArray("saved_networks")
        if (arr != null) {
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                saved.add(
                    SavedNetUi(
                        o.optString("id"),
                        o.optString("uuid"),
                        o.optCleanString("ssid") ?: "",
                        nmAutoconnect = o.optBoolean("autoconnect", false),
                    ),
                )
            }
        }
        return StatusUi(
            wifiRole = j.optCleanString("wifi_role") ?: "—",
            ipv4 = j.optCleanString("ipv4"),
            userMode = j.optCleanString("user_mode") ?: "—",
            bootWaitRemainingSec = j.optInt("boot_wait_remaining_sec"),
            clientSeen = j.optBoolean("client_seen"),
            lastError = j.optCleanString("last_error"),
            savedNetworks = saved,
            apSsid = j.optCleanString("ap_ssid"),
            activeStaSsid = j.optCleanString("active_sta_ssid"),
            activeStaProfile = j.optCleanString("active_sta_profile"),
            paired = j.optBoolean("paired"),
        )
    }

    suspend fun fetchRecordStatus(): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            client.recordStatus(url)
        } catch (_: Exception) {
            null
        }

    suspend fun startRemoteRecord(
        name: String,
        seconds: Double = 5.0,
        hz: Double = 20.0,
        countdown: Double = 3.0,
        holdAfter: Boolean = false,
        register: Boolean = true,
    ): String? {
        return try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            val resp =
                client.recordStart(url, bearer, name.trim(), seconds, hz, countdown, holdAfter, register)
            if (!resp.optBoolean("ok", true)) {
                resp.jsonCleanString("error")
                    ?: "Recording did not start (server rejected request)."
            } else {
                null
            }
        } catch (e: LinkApiException) {
            normalizeRemoteError(e)
        } catch (e: Exception) {
            normalizeRemoteError(e)
        }
    }

    suspend fun stopRemoteRecord(): String? {
        return try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            val resp = client.recordStop(url, bearer)
            if (!resp.optBoolean("ok", true)) {
                resp.jsonCleanString("error") ?: "Could not stop recording."
            } else {
                null
            }
        } catch (e: LinkApiException) {
            normalizeRemoteError(e)
        } catch (e: Exception) {
            normalizeRemoteError(e)
        }
    }

    suspend fun deleteManifestAction(
        actionName: String,
        deleteRecording: Boolean = true,
        deleteAudio: Boolean = false,
    ): String? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.deleteManifestAction(url, bearer, actionName, deleteRecording, deleteAudio)
            null
        } catch (e: LinkApiException) {
            normalizeRemoteError(e)
        } catch (e: Exception) {
            normalizeRemoteError(e)
        }

    private fun normalizeRemoteError(e: Exception): String =
        when (e) {
            is LinkApiException -> friendlyHttp(e)
            else -> {
                val m = e.message?.trim()
                if (m.isNullOrBlank() || m.equals("null", ignoreCase = true)) {
                    "Request failed (${e.javaClass.simpleName})"
                } else {
                    m
                }
            }
        }

    suspend fun fetchDaemonHealth(): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            client.health(url)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchActionAudioInfo(action: String): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            client.actionAudioInfo(url, action)
        } catch (_: Exception) {
            null
        }

    suspend fun postActionAudioOffset(action: String, audioOffsetSec: Double): String? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.actionAudioOffset(url, bearer, action, audioOffsetSec)
            null
        } catch (e: Exception) {
            e.message
        }

    suspend fun postActionAudioClear(action: String): String? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.actionAudioClear(url, bearer, action)
            null
        } catch (e: Exception) {
            e.message
        }

    suspend fun postActionAudioGenerate(
        action: String,
        text: String,
        lang: String,
        tld: String,
        audioOffsetSec: Double,
        slow: Boolean = false,
    ): String? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.actionAudioGenerate(url, bearer, action, text, lang, tld, audioOffsetSec, slow)
            null
        } catch (e: Exception) {
            e.message
        }

    suspend fun fetchVisionStatus(): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            client.visionStatus(url)
        } catch (_: Exception) {
            null
        }

    /** Apply vision toggles and return the daemon JSON (includes toggle_*_error on failure). */
    suspend fun postVisionOptionsSync(
        face: Boolean?,
        objects: Boolean?,
        objectConfidence: Double? = null,
    ): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.visionOptions(url, bearer, face, objects, objectConfidence)
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (e: Exception) {
            NinaLog.warn("vision_options", e.message ?: "failed")
            null
        }

    suspend fun visionOpen(): String? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.visionOpen(url, bearer)
            null
        } catch (e: Exception) {
            e.message
        }

    suspend fun visionStop(): String? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.visionStop(url, bearer)
            null
        } catch (e: Exception) {
            e.message
        }

    /** Start face enrollment; second value is a human-readable network/auth error when present. */
    suspend fun visionEnroll(name: String, targetSamples: Int = 8): Pair<JSONObject?, String?> =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            Pair(client.visionEnroll(url, bearer, name, targetSamples), null)
        } catch (e: LinkApiException) {
            Pair(null, friendlyHttp(e))
        } catch (e: Exception) {
            Pair(null, normalizeRemoteError(e))
        }

    suspend fun fetchVisionEnrollStatus(): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            client.visionEnrollStatus(url)
        } catch (_: Exception) {
            null
        }

    suspend fun visionAnnounceObjects(): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.visionAnnounce(url, bearer)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchVisionAnnounceStatus(): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            client.visionAnnounceStatus(url)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchVisionDetections(): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            client.visionDetections(url)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchSlamStatus(): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            client.slamStatus(url)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchSlamSnapshot(): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            client.slamSnapshot(url)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchSlamOccupancyGrid(): SlamOccupancyGrid? =
        try {
            val url = prefs.baseUrl.first()
            client.slamOccupancyGrid(url)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchRobotHealth(): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            client.robotHealth(url)
        } catch (_: Exception) {
            null
        }

    suspend fun saveSlamMapPgm(filename: String): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.slamSave(url, bearer, filename)
        } catch (e: LinkApiException) {
            JSONObject().put("ok", false).put("detail", e.message ?: "HTTP ${e.code}")
        } catch (_: Exception) {
            null
        }

    suspend fun fetchDepthStatus(): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            client.depthStatus(url)
        } catch (_: Exception) {
            null
        }

    suspend fun fetchAutonomyStatus(): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            client.autonomyStatus(url)
        } catch (_: Exception) {
            null
        }

    suspend fun postAutonomyEnabled(enabled: Boolean): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.setAutonomyEnabled(url, bearer, enabled)
        } catch (_: Exception) {
            null
        }

    /** POST /v1/autonomy/goal — arm goto with the given world-mm coordinates. */
    suspend fun postAutonomyGoal(xMm: Double, yMm: Double): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.setAutonomyGoal(url, bearer, xMm, yMm)
        } catch (_: Exception) {
            null
        }

    /** DELETE /v1/autonomy/goal — cancel an in-flight goto. */
    suspend fun deleteAutonomyGoal(): JSONObject? =
        try {
            val url = prefs.baseUrl.first()
            val bearer = prefs.bearerToken.first()
            client.clearAutonomyGoal(url, bearer)
        } catch (_: Exception) {
            null
        }

    fun sessionClaim(onResult: (String?) -> Unit) {
        viewModelScope.launch {
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                client.sessionClaim(url, bearer)
                robotConsoleSessionActive = true
                onResult(null)
            } catch (e: LinkApiException) {
                if (e.code == 503) {
                    onResult(null)
                } else {
                    onResult(e.message)
                }
            } catch (e: Exception) {
                onResult(e.message)
            }
        }
    }

    fun sessionRelease(onResult: (String?) -> Unit) {
        viewModelScope.launch {
            try {
                val url = prefs.baseUrl.first()
                val bearer = prefs.bearerToken.first()
                client.sessionRelease(url, bearer)
                onResult(null)
            } catch (e: Exception) {
                onResult(e.message)
            } finally {
                robotConsoleSessionActive = false
            }
        }
    }

    /**
     * Opening the full Nina console should pause the on-robot kiosk so `nina-link` can open USB/GPIO
     * (see `NINA_LINK_SESSION_SCRIPT` on the Jetson). Closing the console releases.
     */
    fun notifyRobotConsoleVisibility(visible: Boolean) {
        viewModelScope.launch {
            if (visible) {
                if (robotConsoleSessionActive) return@launch
                try {
                    val url = prefs.baseUrl.first()
                    val bearer = prefs.bearerToken.first()
                    client.sessionClaim(url, bearer)
                    robotConsoleSessionActive = true
                    NinaLog.api("session_claim", "robot console opened")
                } catch (e: LinkApiException) {
                    if (e.code != 503) {
                        NinaLog.warn("Session", e.message ?: "claim")
                    }
                } catch (e: Exception) {
                    NinaLog.warn("Session", e.message ?: "claim")
                }
            } else {
                if (!robotConsoleSessionActive) return@launch
                try {
                    val url = prefs.baseUrl.first()
                    val bearer = prefs.bearerToken.first()
                    client.sessionRelease(url, bearer)
                    NinaLog.api("session_release", "robot console closed")
                } catch (e: Exception) {
                    NinaLog.warn("Session", e.message ?: "release")
                } finally {
                    robotConsoleSessionActive = false
                }
            }
        }
    }

    override fun onCleared() {
        if (robotConsoleSessionActive) {
            runBlocking {
                try {
                    val url = prefs.baseUrl.first()
                    val bearer = prefs.bearerToken.first()
                    client.sessionRelease(url, bearer)
                } catch (_: Exception) {
                    // best-effort — process is dying
                } finally {
                    robotConsoleSessionActive = false
                }
            }
        }
        super.onCleared()
    }

    suspend fun mediaFileUrl(relativePath: String): String {
        val base = prefs.baseUrl.first().trimEnd('/')
        val enc = java.net.URLEncoder.encode(relativePath, Charsets.UTF_8.toString())
        return "$base/v1/media/file?relative=$enc"
    }

    private fun friendlyHttp(e: LinkApiException): String {
        val raw = e.message?.trim()
        val cleaned =
            if (raw.isNullOrBlank() || raw.equals("null", ignoreCase = true)) {
                null
            } else {
                raw
            }
        if (e.code == 401) {
            return "Unauthorized — set a fleet token or pair with PIN (Setup tab)."
        }
        return cleaned ?: "HTTP ${e.code}"
    }
}

/** JSON string fields: treat blank and literal `"null"` as absent (some intermediaries stringify null). */
private fun JSONObject.optCleanString(key: String): String? {
    if (!has(key) || isNull(key)) return null
    val s = optString(key).trim()
    if (s.isEmpty() || s.equals("null", ignoreCase = true)) return null
    return s
}
