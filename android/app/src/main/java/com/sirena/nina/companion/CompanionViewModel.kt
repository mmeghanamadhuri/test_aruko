package com.sirena.nina.companion

import android.app.Application
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.sirena.nina.companion.data.LinkApiException
import com.sirena.nina.companion.data.LinkClient
import com.sirena.nina.companion.data.Prefs
import com.sirena.nina.companion.network.DaemonUrlResolver
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
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
)

data class SavedNetUi(
    val id: String,
    val uuid: String,
    val ssid: String,
    val nmAutoconnect: Boolean,
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

    /** Persisted daemon URL (normalized). */
    val savedDaemonUrl: Flow<String> = prefs.baseUrl

    private val _gatewayHint = MutableStateFlow<String?>(null)
    val gatewayHint: StateFlow<String?> = _gatewayHint.asStateFlow()

    private val _state = MutableStateFlow<CompanionUiState>(CompanionUiState.Loading)
    val state: StateFlow<CompanionUiState> = _state.asStateFlow()

    init {
        refreshStatus()
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
                _state.update {
                    CompanionUiState.Error(friendlyHttp(e))
                }
            } catch (e: Exception) {
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
        val candidates = buildCandidateUrls()
        var lastError: Exception? = null
        for (url in candidates) {
            try {
                assertUrlNotTabletOwnIp(url)
                client.health(url)
                val st = client.status(url, bearer)
                prefs.setBaseUrl(url)
                return url to parseStatus(st)
            } catch (e: IllegalArgumentException) {
                lastError = e
                continue
            } catch (e: Exception) {
                lastError = e
                continue
            }
        }
        throw lastError ?: IllegalStateException("Could not reach Nina Link.")
    }

    private fun buildDiscoveryHint(myIp: String?, gw: String?): String {
        return when {
            DaemonUrlResolver.isTypicalHomeLanClient(myIp) ->
                "Home Wi‑Fi: the default gateway (${gw ?: "router"}) is usually not the Jetson. " +
                    "Under Setup, set Daemon URL to the robot's address (same subnet as this tablet, " +
                    "e.g. http://192.168.1.x:8787), then Save & test."
            gw != null ->
                "Wi‑Fi gateway: http://$gw:8787 (use Setup if that is your router, not the robot)."
            else ->
                "Open Setup and enter the Jetson link-daemon URL if discovery fails."
        }
    }

    private suspend fun buildCandidateUrls(): List<String> {
        val myIp = DaemonUrlResolver.deviceIpv4(appCtx)
        val gw = DaemonUrlResolver.gatewayIpv4(appCtx)
        val savedNorm = Prefs.normalizeBaseUrl(prefs.baseUrl.first())
        val hotspot = DaemonUrlResolver.isNinaHotspotClient(myIp)
        val homeLan = DaemonUrlResolver.isTypicalHomeLanClient(myIp)

        val candidates = mutableListOf<String>()

        fun offer(raw: String) {
            val n = Prefs.normalizeBaseUrl(raw)
            val host = Uri.parse(n).host ?: return
            if (host.equals(myIp, ignoreCase = true)) return
            if (n !in candidates) candidates.add(n)
        }

        // On home/office LAN the DHCP gateway is almost always the router, not the Jetson —
        // try the saved URL first (operator sets Jetson LAN IP under Setup).
        if (homeLan) {
            offer(savedNorm)
        }

        if (gw != null && !gw.equals(myIp, ignoreCase = true)) {
            offer("http://$gw:8787")
        }

        if (!homeLan) {
            offer(savedNorm)
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
                    "Use the Router/Gateway IP from Wi‑Fi details (often ends in .1), " +
                    "e.g. http://10.42.0.1:8787",
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

    /** Momentary drive pulse — requires Jetson `NINA_LINK_ENABLE_ROBOT_BRIDGE=1`. */
    suspend fun robotDriveMomentary(direction: String, durationMs: Int = 280) {
        val url = prefs.baseUrl.first()
        val bearer = prefs.bearerToken.first()
        client.robotDriveMomentary(url, bearer, direction, durationMs, null)
    }

    suspend fun robotEmergencyStop() {
        val url = prefs.baseUrl.first()
        val bearer = prefs.bearerToken.first()
        client.robotEmergencyStop(url, bearer)
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
                        o.optString("ssid"),
                        nmAutoconnect = o.optBoolean("autoconnect", false),
                    ),
                )
            }
        }
        return StatusUi(
            wifiRole = j.optString("wifi_role", "—"),
            ipv4 = j.optString("ipv4").takeIf { it.isNotBlank() },
            userMode = j.optString("user_mode", "—"),
            bootWaitRemainingSec = j.optInt("boot_wait_remaining_sec"),
            clientSeen = j.optBoolean("client_seen"),
            lastError = j.optString("last_error").takeIf { it.isNotBlank() },
            savedNetworks = saved,
            apSsid = j.optString("ap_ssid").takeIf { it.isNotBlank() },
            activeStaSsid = j.optString("active_sta_ssid").takeIf { it.isNotBlank() },
            activeStaProfile = j.optString("active_sta_profile").takeIf { it.isNotBlank() },
        )
    }

    private fun friendlyHttp(e: LinkApiException): String =
        if (e.code == 401) {
            "Unauthorized — set a fleet token or pair with PIN (Setup tab)."
        } else {
            e.message ?: "HTTP ${e.code}"
        }
}
