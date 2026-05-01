package com.sirena.nina.companion.ui

import android.content.Intent
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.DirectionsCar
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Wifi
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sirena.nina.companion.CompanionUiState
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.StatusUi
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NinaApp(vm: CompanionViewModel) {
    val state by vm.state.collectAsStateWithLifecycle()
    var tab by rememberSaveable { mutableIntStateOf(0) }
    val snack = remember { SnackbarHostState() }

    Scaffold(
        snackbarHost = { SnackbarHost(snack) },
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        "Nina Companion",
                        fontWeight = FontWeight.SemiBold,
                    )
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.primary,
                    titleContentColor = MaterialTheme.colorScheme.onPrimary,
                ),
            )
        },
        bottomBar = {
            NavigationBar {
                NavigationBarItem(
                    selected = tab == 0,
                    onClick = { tab = 0 },
                    icon = { Icon(Icons.Default.Home, null) },
                    label = { Text("Home") },
                )
                NavigationBarItem(
                    selected = tab == 1,
                    onClick = { tab = 1 },
                    icon = { Icon(Icons.Default.Wifi, null) },
                    label = { Text("Networks") },
                )
                NavigationBarItem(
                    selected = tab == 2,
                    onClick = { tab = 2 },
                    icon = { Icon(Icons.Default.DirectionsCar, null) },
                    label = { Text("Drive") },
                )
                NavigationBarItem(
                    selected = tab == 3,
                    onClick = { tab = 3 },
                    icon = { Icon(Icons.Default.Settings, null) },
                    label = { Text("Setup") },
                )
            }
        },
    ) { padding ->
        Column(
            Modifier
                .padding(padding)
                .fillMaxSize(),
        ) {
            when (tab) {
                0 -> HomeTab(state, vm, snack)
                1 -> NetworksTab(state, vm)
                2 -> DriveTab()
                3 -> SetupTab(vm, snack)
            }
        }
    }

    LaunchedEffect(state) {
        if (state is CompanionUiState.Error) {
            snack.showSnackbar((state as CompanionUiState.Error).text)
        }
    }
}

@Composable
private fun HomeTab(
    state: CompanionUiState,
    vm: CompanionViewModel,
    snack: SnackbarHostState,
) {
    val scope = rememberCoroutineScope()
    var ssid by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    val ctx = LocalContext.current

    LazyColumn(
        Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Text(
                "Connection & provisioning",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
            )
            Text(
                "On the Jetson access-point (AP), open this app while connected to the Nina AP. " +
                    "Save home Wi‑Fi on the Jetson, tap “Connect Jetson to home Wi‑Fi”, then use " +
                    "the button below to switch this tablet to the same network.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        item {
            OutlinedButton(
                onClick = {
                    ctx.startActivity(Intent(Settings.ACTION_WIFI_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    scope.launch {
                        snack.showSnackbar(
                            "Join the same SSID as the Jetson, then return here and tap Refresh.",
                        )
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Open Android Wi‑Fi settings")
            }
        }
        item {
            Button(
                onClick = { vm.refreshStatus() },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Refresh status")
            }
        }
        when (state) {
            CompanionUiState.Loading -> item {
                Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
                    CircularProgressIndicator()
                }
            }
            is CompanionUiState.Error -> item {
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)) {
                    Text(
                        (state as CompanionUiState.Error).text,
                        Modifier.padding(12.dp),
                        color = MaterialTheme.colorScheme.onErrorContainer,
                    )
                }
            }
            is CompanionUiState.Ready -> {
                val st = (state as CompanionUiState.Ready).status
                if (st != null) {
                    item { StatusCard(st) }
                }
                item {
                    Text("Home network (saved on Jetson)", fontWeight = FontWeight.Medium)
                    OutlinedTextField(
                        ssid,
                        onValueChange = { ssid = it },
                        label = { Text("SSID") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                    )
                    Spacer(Modifier.height(8.dp))
                    OutlinedTextField(
                        password,
                        onValueChange = { password = it },
                        label = { Text("Password") },
                        modifier = Modifier.fillMaxWidth(),
                        singleLine = true,
                    )
                    Spacer(Modifier.height(8.dp))
                    Button(
                        onClick = { vm.saveHomeAndOptionallyConnect(ssid, password, connect = false) },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Save credentials on Jetson only")
                    }
                    OutlinedButton(
                        onClick = { vm.saveHomeAndOptionallyConnect(ssid, password, connect = true) },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Save on Jetson & connect Jetson to this SSID")
                    }
                    OutlinedButton(
                        onClick = { vm.connectJetsonHome(null) },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Connect Jetson using first saved profile")
                    }
                    OutlinedButton(
                        onClick = { vm.startApOnJetson() },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Force Jetson back to AP mode")
                    }
                }
            }
        }
    }
}

@Composable
private fun StatusCard(st: StatusUi) {
    Card(Modifier.fillMaxWidth(), colors = CardDefaults.cardColors()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Jetson link status", fontWeight = FontWeight.SemiBold)
            Text("Wi‑Fi role: ${st.wifiRole}")
            Text("IPv4: ${st.ipv4 ?: "—"}")
            Text("User mode: ${st.userMode}")
            Text("Boot window: ${st.bootWaitRemainingSec}s")
            Text("Client seen: ${st.clientSeen}")
            st.apSsid?.let { Text("AP SSID: $it") }
            st.lastError?.let {
                Text("Last error: $it", color = MaterialTheme.colorScheme.error)
            }
        }
    }
}

@Composable
private fun NetworksTab(state: CompanionUiState, vm: CompanionViewModel) {
    Column(
        Modifier
            .fillMaxSize()
            .padding(16.dp),
    ) {
        Text(
            "Saved on Jetson",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
        )
        Spacer(Modifier.height(8.dp))
        when (state) {
            is CompanionUiState.Ready -> {
                val list = state.status?.savedNetworks.orEmpty()
                if (list.isEmpty()) {
                    Text(
                        "No profiles yet. Save home Wi‑Fi from the Home tab.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                } else {
                    LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        items(list, key = { it.uuid }) { net ->
                            Card(Modifier.fillMaxWidth()) {
                                Column(Modifier.padding(12.dp)) {
                                    Text(net.ssid, fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
                                    Text(
                                        net.uuid,
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                    TextButton(onClick = { vm.deleteProfile(net.id) }) {
                                        Text("Remove from Jetson")
                                    }
                                }
                            }
                        }
                    }
                }
            }
            else -> Text("Load status from Home tab first.")
        }
    }
}

@Composable
private fun DriveTab() {
    Column(
        Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Drive", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
        Text(
            "Drive refinement is in progress on the robot. This tab will mirror the Sirena UI " +
                "drive controls over the link API in a future update.",
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp)) {
                Text("Capabilities", fontWeight = FontWeight.Medium)
                Text("drive: preview")
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SetupTab(vm: CompanionViewModel, snack: SnackbarHostState) {
    val scope = rememberCoroutineScope()
    var url by remember { mutableStateOf("http://192.168.4.1:8787") }
    var bearer by remember { mutableStateOf("") }
    var pin by remember { mutableStateOf("") }
    var showToken by remember { mutableStateOf<String?>(null) }

    if (showToken != null) {
        AlertDialog(
            onDismissRequest = { showToken = null },
            confirmButton = {
                TextButton(onClick = { showToken = null }) { Text("OK") }
            },
            title = { Text("Session token saved") },
            text = { Text("Stored for API calls. You can paste a token manually below if needed.") },
        )
    }

    LazyColumn(
        Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Text("Daemon URL", fontWeight = FontWeight.Medium)
            OutlinedTextField(
                url,
                onValueChange = { url = it },
                label = { Text("http://host:8787") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            Button(
                onClick = {
                    vm.saveBaseUrl(url)
                    vm.ping(url)
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Save & test connection")
            }
        }
        item {
            Text(
                "Optional: fleet token (NINA_LINK_TOKEN on Jetson) or pairing PIN from the robot screen.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedTextField(
                bearer,
                onValueChange = { bearer = it },
                label = { Text("Bearer token") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            Button(
                onClick = { vm.saveBearer(bearer.takeIf { it.isNotBlank() }) },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Save bearer token")
            }
        }
        item {
            OutlinedTextField(
                pin,
                onValueChange = { pin = it },
                label = { Text("Pairing PIN (shown on Jetson Settings → Network)") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            Button(
                onClick = {
                    vm.pair(pin) { tok ->
                        showToken = tok
                        scope.launch { snack.showSnackbar("Paired; token stored.") }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Pair with PIN")
            }
        }
        item {
            Text("Mode override", fontWeight = FontWeight.Medium)
            Text(
                "Sends the same user_mode as Sirena Settings on the Jetson.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { vm.setMode("boot_default") }, Modifier.fillMaxWidth()) {
                    Text("boot_default")
                }
                OutlinedButton(onClick = { vm.setMode("force_ap") }, Modifier.fillMaxWidth()) {
                    Text("force_ap (stay on AP)")
                }
                OutlinedButton(onClick = { vm.setMode("force_sta") }, Modifier.fillMaxWidth()) {
                    Text("force_sta (use saved Wi‑Fi)")
                }
            }
        }
    }
}
