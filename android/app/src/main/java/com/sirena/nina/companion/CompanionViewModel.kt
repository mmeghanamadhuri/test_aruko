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
                _gatewayHint.value = gw?.let { "Wi‑Fi gateway (Jetson): http://$it:8787" }
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

    private suspend fun buildCandidateUrls(): List<String> {
        val ordered = LinkedHashSet<String>()
        val myIp = DaemonUrlResolver.deviceIpv4(appCtx)
        val gw = DaemonUrlResolver.gatewayIpv4(appCtx)
        val gwUrl = gw?.let { Prefs.normalizeBaseUrl("http://$it:8787") }

        // 1) Gateway from route table (Jetson), never this tablet's address
        if (gwUrl != null && gw != null && !gw.equals(myIp, ignoreCase = true)) {
            ordered.add(gwUrl)
        }

        // 2) NM / tether subnet heuristics when routes are empty or misleading
        DaemonUrlResolver.heuristicGatewayForDeviceIp(myIp)?.let { hint ->
            ordered.add(Prefs.normalizeBaseUrl("http://$hint:8787"))
        }

        // 3) Saved URL unless it mistakenly targets this device
        val savedNorm = Prefs.normalizeBaseUrl(prefs.baseUrl.first())
        val savedHost = Uri.parse(savedNorm).host
        if (!savedHost.isNullOrBlank() && !savedHost.equals(myIp, ignoreCase = true)) {
            ordered.add(savedNorm)
        }

        // 4) Common Jetson / hotspot gateways
        ordered.add(Prefs.normalizeBaseUrl("http://10.42.0.1:8787"))
        ordered.add(Prefs.normalizeBaseUrl("http://192.168.4.1:8787"))

        return ordered.toList()
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
            "Unauthorized — set a fleet token or pair with PIN (Settings tab)."
        } else {
            e.message ?: "HTTP ${e.code}"
        }
}
