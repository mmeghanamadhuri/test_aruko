package com.sirena.nina.companion.ui

import android.content.ClipData
import android.content.Intent
import android.provider.Settings
import androidx.core.content.FileProvider
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
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
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sirena.nina.companion.R
import com.sirena.nina.companion.CompanionUiState
import com.sirena.nina.companion.CompanionViewModel
import com.sirena.nina.companion.StatusUi
import com.sirena.nina.companion.data.Prefs
import com.sirena.nina.companion.util.NinaFileLogger
import com.sirena.nina.companion.util.NinaLog
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NinaApp(vm: CompanionViewModel) {
    val state by vm.state.collectAsStateWithLifecycle()
    var tab by rememberSaveable { mutableIntStateOf(0) }
    var showNinaConsole by rememberSaveable { mutableStateOf(false) }
    val snack = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    Box(Modifier.fillMaxSize()) {
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
                    onClick = {
                        NinaLog.tap("Main", "bottom_nav", "Home")
                        tab = 0
                    },
                    icon = { Icon(Icons.Default.Home, null) },
                    label = { Text("Home") },
                )
                NavigationBarItem(
                    selected = tab == 1,
                    onClick = {
                        NinaLog.tap("Main", "bottom_nav", "Networks")
                        tab = 1
                    },
                    icon = { Icon(Icons.Default.Wifi, null) },
                    label = { Text("Networks") },
                )
                NavigationBarItem(
                    selected = tab == 2,
                    onClick = {
                        NinaLog.tap("Main", "bottom_nav", "Setup")
                        tab = 2
                    },
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
                0 -> HomeTab(
                    state = state,
                    vm = vm,
                    snack = snack,
                    onOpenNinaConsole = { showNinaConsole = true },
                    onOpenCarbotPlaceholder = {
                        scope.launch {
                            snack.showSnackbar("Carbot motor bridge — coming soon.")
                        }
                    },
                    onOpenSystemSetup = { tab = 2 },
                )
                1 -> NetworksTab(state = state, vm = vm, snack = snack)
                2 -> SetupTab(vm = vm, snack = snack)
            }
        }
        }

        if (showNinaConsole) {
            NinaConsoleScreen(
                vm = vm,
                state = state,
                onBack = { showNinaConsole = false },
                modifier = Modifier.fillMaxSize(),
            )
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
    onOpenNinaConsole: () -> Unit,
    onOpenCarbotPlaceholder: () -> Unit,
    onOpenSystemSetup: () -> Unit,
) {
    val gatewayHint by vm.gatewayHint.collectAsStateWithLifecycle(null)
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()

    LazyColumn(
        Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Text(
                "Dashboard",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.Bold,
            )
            Text(
                "Choose a control surface. Nina opens the same feature areas as the robot touchscreen UI.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        item {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                DashboardCard(
                    title = "Carbot",
                    subtitle = "Mobility / motor bridge (Pi)",
                    placeholderLabel = "Carbot",
                    modifier = Modifier.weight(1f),
                    heroDrawableId = R.drawable.sirena_logo,
                    onClick = onOpenCarbotPlaceholder,
                )
                DashboardCard(
                    title = "Nina",
                    subtitle = "Arms, vision, drive, actions",
                    placeholderLabel = "Nina",
                    modifier = Modifier.weight(1f),
                    heroDrawableId = R.drawable.nina_hero,
                    onClick = onOpenNinaConsole,
                )
                DashboardCard(
                    title = "System",
                    subtitle = "Wi‑Fi, link, provisioning",
                    placeholderLabel = "System",
                    modifier = Modifier.weight(1f),
                    heroDrawableId = null,
                    onClick = onOpenSystemSetup,
                )
            }
        }
        item {
            gatewayHint?.let { hint ->
                Text(
                    hint,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
        }
        item {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedButton(
                    onClick = {
                        NinaLog.tap("Dashboard", "wifi_settings")
                        ctx.startActivity(
                            Intent(Settings.ACTION_WIFI_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                        )
                        scope.launch {
                            snack.showSnackbar(
                                "Join the Jetson Wi‑Fi, then return and tap Refresh.",
                            )
                        }
                    },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Wi‑Fi settings")
                }
                Button(
                    onClick = {
                        NinaLog.tap("Dashboard", "refresh_status")
                        vm.refreshStatus()
                    },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Refresh status")
                }
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
                val ready = state as CompanionUiState.Ready
                val st = ready.status
                if (st != null && st.wifiRole == "ap") {
                    item {
                        Card(
                            Modifier.fillMaxWidth(),
                            colors = CardDefaults.cardColors(
                                containerColor = MaterialTheme.colorScheme.primaryContainer,
                            ),
                        ) {
                            Text(
                                "Connected to Nina Link on the robot access point.",
                                Modifier.padding(16.dp),
                                color = MaterialTheme.colorScheme.onPrimaryContainer,
                            )
                        }
                    }
                }
                if (st != null) {
                    item {
                        Text(
                            "Daemon: ${ready.url}",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        StatusCard(st)
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DashboardCard(
    title: String,
    subtitle: String,
    placeholderLabel: String,
    modifier: Modifier = Modifier,
    heroDrawableId: Int? = null,
    onClick: () -> Unit,
) {
    Card(
        onClick = {
            NinaLog.tap("Dashboard", "card", title)
            onClick()
        },
        modifier = modifier.height(200.dp),
    ) {
        Column(
            Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant,
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .height(72.dp),
            ) {
                Box(
                    Modifier.fillMaxSize(),
                    contentAlignment = Alignment.Center,
                ) {
                    if (heroDrawableId != null) {
                        Image(
                            painter = painterResource(heroDrawableId),
                            contentDescription = title,
                            modifier = Modifier
                                .fillMaxSize()
                                .padding(8.dp),
                            contentScale = ContentScale.Fit,
                        )
                    } else {
                        Text(
                            placeholderLabel,
                            style = MaterialTheme.typography.labelLarge,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
            Text(title, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleMedium)
            Text(
                subtitle,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
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
            if (!st.activeStaSsid.isNullOrBlank()) {
                Text("Connected (STA): ${st.activeStaSsid}")
                st.activeStaProfile?.let { Text("NM profile: $it") }
            }
            st.lastError?.let {
                Text("Last error: $it", color = MaterialTheme.colorScheme.error)
            }
        }
    }
}

@Composable
private fun NetworksTab(
    state: CompanionUiState,
    vm: CompanionViewModel,
    snack: SnackbarHostState,
) {
    val scope = rememberCoroutineScope()
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
        Text(
            "Connect Jetson asks the robot to join that profile (STA). Then join the same Wi‑Fi on " +
                "this tablet and tap Refresh on Home.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(12.dp))
        when (state) {
            is CompanionUiState.Ready -> {
                val list = state.status?.savedNetworks.orEmpty()
                if (list.isEmpty()) {
                    Text(
                        "No profiles yet. Add home Wi‑Fi under Setup.",
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
                                    Text(
                                        if (net.nmAutoconnect) "NM autoconnect: on" else "NM autoconnect: off",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                    Spacer(Modifier.height(8.dp))
                                    Row(
                                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                                        modifier = Modifier.fillMaxWidth(),
                                    ) {
                                        Button(
                                            onClick = {
                                                NinaLog.tap("Networks", "connect_jetson", net.ssid)
                                                vm.connectJetsonHome(net.ssid)
                                                scope.launch {
                                                    snack.showSnackbar(
                                                        "Jetson is connecting to “${net.ssid}”. " +
                                                            "When online, join that Wi‑Fi on this tablet, open the app, tap Refresh.",
                                                    )
                                                }
                                            },
                                            modifier = Modifier.weight(1f),
                                        ) {
                                            Text("Connect Jetson")
                                        }
                                        OutlinedButton(
                                            onClick = {
                                                NinaLog.tap("Networks", "delete_profile", net.ssid)
                                                vm.deleteProfile(net.id)
                                            },
                                            modifier = Modifier.weight(1f),
                                        ) {
                                            Text("Remove")
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            else -> Text("Connect to the robot first (Refresh on Home).")
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SetupTab(vm: CompanionViewModel, snack: SnackbarHostState) {
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    val state by vm.state.collectAsStateWithLifecycle()
    val savedUrl by vm.savedDaemonUrl.collectAsStateWithLifecycle(Prefs.DEFAULT_BASE_URL)
    var urlDraft by remember { mutableStateOf<String?>(null) }
    val url = urlDraft ?: savedUrl
    var bearer by remember { mutableStateOf("") }
    var pin by remember { mutableStateOf("") }
    var showToken by remember { mutableStateOf<String?>(null) }
    var ssid by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }

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
            Text("Provisioning", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleMedium)
            Text(
                "Save home Wi‑Fi on the Jetson and switch modes — same flows as on-robot Network settings.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (state is CompanionUiState.Ready) {
            val st = (state as CompanionUiState.Ready).status
            if (st != null) {
                item { StatusCard(st) }
            }
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
                onClick = {
                    NinaLog.tap("Setup", "save_wifi_credentials_only")
                    vm.saveHomeAndOptionallyConnect(ssid, password, connect = false)
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Save credentials on Jetson only")
            }
            OutlinedButton(
                onClick = {
                    NinaLog.tap("Setup", "save_wifi_and_connect_jetson")
                    vm.saveHomeAndOptionallyConnect(ssid, password, connect = true)
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Save on Jetson & connect Jetson to this SSID")
            }
            OutlinedButton(
                onClick = {
                    NinaLog.tap("Setup", "connect_jetson_first_saved")
                    vm.connectJetsonHome(null)
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Connect Jetson using first saved profile")
            }
            OutlinedButton(
                onClick = {
                    NinaLog.tap("Setup", "force_ap_on_jetson")
                    vm.startApOnJetson()
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Force Jetson back to AP mode")
            }
        }
        item {
            Text("Daemon URL", fontWeight = FontWeight.Medium)
            OutlinedTextField(
                url,
                onValueChange = { urlDraft = it },
                label = { Text("http://gateway:8787") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            Text(
                "Use the Wi‑Fi gateway (e.g. http://10.42.0.1:8787 on Nina AP). Never this tablet's IP.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Button(
                onClick = {
                    NinaLog.tap("Setup", "save_and_test_daemon_url")
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
                "Optional: fleet token or pairing PIN.",
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
                onClick = {
                    NinaLog.tap("Setup", "save_bearer")
                    vm.saveBearer(bearer.takeIf { it.isNotBlank() })
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Save bearer token")
            }
        }
        item {
            OutlinedTextField(
                pin,
                onValueChange = { pin = it },
                label = { Text("Pairing PIN (Jetson Settings → Network)") },
                modifier = Modifier.fillMaxWidth(),
                singleLine = true,
            )
            Button(
                onClick = {
                    NinaLog.tap("Setup", "pair_pin")
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
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = {
                        NinaLog.tap("Setup", "mode", "boot_default")
                        vm.setMode("boot_default")
                    },
                    Modifier.fillMaxWidth(),
                ) {
                    Text("boot_default")
                }
                OutlinedButton(
                    onClick = {
                        NinaLog.tap("Setup", "mode", "force_ap")
                        vm.setMode("force_ap")
                    },
                    Modifier.fillMaxWidth(),
                ) {
                    Text("force_ap (stay on AP)")
                }
                OutlinedButton(
                    onClick = {
                        NinaLog.tap("Setup", "mode", "force_sta")
                        vm.setMode("force_sta")
                    },
                    Modifier.fillMaxWidth(),
                ) {
                    Text("force_sta (use saved Wi‑Fi)")
                }
            }
        }
        item {
            Text("Session log (on-device)", fontWeight = FontWeight.Medium)
            Text(
                "Taps and API events are appended under app-private storage (no extra permission).",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                NinaFileLogger.activeLogFile(ctx).absolutePath,
                style = MaterialTheme.typography.bodySmall,
                fontFamily = FontFamily.Monospace,
                modifier = Modifier.padding(vertical = 4.dp),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            OutlinedButton(
                onClick = {
                    NinaLog.tap("Setup", "export_session_log")
                    val f = NinaFileLogger.activeLogFile(ctx)
                    if (!f.exists()) {
                        scope.launch {
                            snack.showSnackbar("No log file yet — use the app, then try again.")
                        }
                        return@OutlinedButton
                    }
                    val uri =
                        FileProvider.getUriForFile(
                            ctx,
                            "${ctx.packageName}.fileprovider",
                            f,
                        )
                    val send =
                        Intent(Intent.ACTION_SEND).apply {
                            type = "text/plain"
                            putExtra(Intent.EXTRA_STREAM, uri)
                            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                            clipData = ClipData.newUri(ctx.contentResolver, "Session log", uri)
                        }
                    ctx.startActivity(Intent.createChooser(send, "Export session log"))
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("Export / share log file")
            }
        }
    }
}
